/**
 * UI Controller - 统一控制器
 * 负责管理所有功能面板的状态，实现"底层逻辑互通"
 * Live2D 和 VRM 的 UI 按钮都通过这个控制器来操作面板
 */

window.UIController = {
    // 面板引用缓存
    settingsPanel: null,
    agentPanel: null,

    // 窗口引用管理（用于设置窗口）
    _openSettingsWindows: {},

    /**
     * 切换设置面板
     * @returns {boolean} 面板是否处于显示状态
     */
    toggleSettings: function() {
        // 查找设置面板（按优先级查找）
        if (!this.settingsPanel) {
            this.settingsPanel =
                document.getElementById('shared-popup-settings') ||    // 共享的设置面板
                document.querySelector('[id*="popup-settings"]') ||    // 任何带 popup-settings 的元素
                document.getElementById('settings-panel') ||           // 旧的设置面板
                document.getElementById('live2d-config-panel');        // Live2D 配置面板
        }

        if (this.settingsPanel) {
            const isCurrentlyHidden = this.settingsPanel.style.display === 'none' ||
                                     this.settingsPanel.style.opacity === '0';

            if (isCurrentlyHidden) {
                // 显示面板
                this.settingsPanel.style.display = 'flex';
                this.settingsPanel.style.zIndex = '100001';
                // 使用 requestAnimationFrame 确保过渡效果生效
                requestAnimationFrame(() => {
                    this.settingsPanel.style.opacity = '1';
                    this.settingsPanel.style.transform = 'translateX(0)';
                });
                console.log('[UIController] 设置面板已显示');
                return true;
            } else {
                // 隐藏面板
                this.settingsPanel.style.opacity = '0';
                this.settingsPanel.style.transform = 'translateX(-10px)';
                setTimeout(() => {
                    if (this.settingsPanel) {
                        this.settingsPanel.style.display = 'none';
                    }
                }, 200); // 等待过渡动画完成
                console.log('[UIController] 设置面板已隐藏');
                return false;
            }
        }

        console.warn('[UIController] 未找到设置面板');
        return false;
    },

    /**
     * 切换 Agent 面板
     * @returns {boolean} 面板是否处于显示状态
     */
    toggleAgent: function() {
        // 查找 Agent 面板
        if (!this.agentPanel) {
            this.agentPanel =
                document.getElementById('shared-popup-agent') ||       // 共享的 Agent 面板
                document.getElementById('live2d-popup-agent') ||       // Live2D 的 Agent 面板
                document.getElementById('live2d-agent-panel');         // 旧的 Agent 面板
        }

        if (this.agentPanel) {
            const isCurrentlyHidden = this.agentPanel.style.display === 'none' ||
                                     this.agentPanel.style.opacity === '0';

            if (isCurrentlyHidden) {
                // 显示面板
                this.agentPanel.style.display = 'flex';
                this.agentPanel.style.zIndex = '100001';
                requestAnimationFrame(() => {
                    this.agentPanel.style.opacity = '1';
                    this.agentPanel.style.transform = 'translateX(0)';
                });
                console.log('[UIController] Agent 面板已显示');
                return true;
            } else {
                // 隐藏面板
                this.agentPanel.style.opacity = '0';
                this.agentPanel.style.transform = 'translateX(-10px)';
                setTimeout(() => {
                    if (this.agentPanel) {
                        this.agentPanel.style.display = 'none';
                    }
                }, 200);
                console.log('[UIController] Agent 面板已隐藏');
                return false;
            }
        }

        console.warn('[UIController] 未找到 Agent 面板');
        return false;
    },

    /**
     * 切换麦克风
     * @param {boolean} active - 是否激活
     * @returns {boolean} 麦克风是否处于激活状态
     */
    toggleMic: function(active) {
        console.log('[UIController] 麦克风状态切换:', active);

        // 发送自定义事件，让 app.js 中的麦克风逻辑处理
        window.dispatchEvent(new CustomEvent('live2d-mic-toggle', {
            detail: { active: active }
        }));

        return active;
    },

    /**
     * 切换屏幕分享
     * @param {boolean} active - 是否激活
     * @returns {boolean} 屏幕分享是否处于激活状态
     */
    toggleScreen: function(active) {
        console.log('[UIController] 屏幕分享状态切换:', active);

        // 发送自定义事件，让 app.js 中的屏幕分享逻辑处理
        window.dispatchEvent(new CustomEvent('live2d-screen-toggle', {
            detail: { active: active }
        }));

        return active;
    },

    /**
     * 关闭指定的弹出框（通过 ID）
     * @param {string} popupId - 弹出框ID（如 'settings', 'agent'）
     */
    closePopupById: function(popupId) {
        const popup = document.getElementById(`shared-popup-${popupId}`) ||
                     document.getElementById(`live2d-popup-${popupId}`);

        if (popup) {
            popup.style.opacity = '0';
            popup.style.transform = 'translateX(-10px)';
            setTimeout(() => {
                if (popup) {
                    popup.style.display = 'none';
                }
            }, 200);
            console.log('[UIController] 已关闭弹出框:', popupId);
        }
    },

    /**
     * 关闭所有设置窗口（用于窗口管理）
     */
    closeAllSettingsWindows: function() {
        console.log('[UIController] 关闭所有设置窗口');
        for (const url in this._openSettingsWindows) {
            const win = this._openSettingsWindows[url];
            if (win && !win.closed) {
                win.close();
            }
            delete this._openSettingsWindows[url];
        }
    },

    /**
     * 获取面板当前状态
     * @param {string} panelType - 面板类型（'settings', 'agent'）
     * @returns {boolean} 面板是否显示
     */
    isPanelVisible: function(panelType) {
        let panel = null;

        if (panelType === 'settings') {
            panel = this.settingsPanel;
        } else if (panelType === 'agent') {
            panel = this.agentPanel;
        }

        if (!panel) return false;

        return panel.style.display !== 'none' && panel.style.opacity !== '0';
    },

    /**
     * 显示弹出框（带动画）
     * @param {string} popupId - 弹出框ID
     * @param {HTMLElement} popup - 弹出框元素
     */
    showPopup: function(popupId, popup) {
        if (!popup) {
            popup = document.getElementById(`shared-popup-${popupId}`) ||
                   document.getElementById(`live2d-popup-${popupId}`);
        }

        if (!popup) {
            console.warn('[UIController] 未找到弹出框:', popupId);
            return;
        }

        const isCurrentlyVisible = popup.style.display === 'flex' && popup.style.opacity === '1';

        if (isCurrentlyVisible) {
            // 当前显示，执行隐藏
            popup.style.opacity = '0';
            popup.style.transform = 'translateX(-10px)';
            setTimeout(() => {
                popup.style.display = 'none';
            }, 200);
        } else {
            // 当前隐藏，执行显示
            popup.style.display = 'flex';
            requestAnimationFrame(() => {
                popup.style.opacity = '1';
                popup.style.transform = 'translateX(0)';
            });
        }
    }
};

// 全局暴露关闭窗口函数（兼容旧代码）
window.closeAllSettingsWindows = function() {
    window.UIController.closeAllSettingsWindows();
};

console.log('[UIController] 控制器已加载');
