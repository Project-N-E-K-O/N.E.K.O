(function (namespace) {
    'use strict';

    const {
        resolveGuideLocale,
        DEFAULT_CURSOR_DURATION_MS,
        DEFAULT_CURSOR_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS,
        PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT,
        PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY,
        PLUGIN_DASHBOARD_WINDOW_NAME,
        PLUGIN_DASHBOARD_HANDOFF_EVENT,
        PLUGIN_DASHBOARD_TERMINATE_EVENT,
        PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
        PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT,
        TAKEOVER_CAPTURE_SELECTORS,
        wait,
        fetchWithTimeout,
        resolveWithTimeout,
        clamp
    } = namespace;

    namespace.extendDirector({
        getHomeInteractionApi() {
            if (this.options && this.options.homeInteractionApi) {
                return this.options.homeInteractionApi;
            }

            if (typeof window.getYuiGuideHomeInteractionApi === 'function') {
                try {
                    return window.getYuiGuideHomeInteractionApi() || null;
                } catch (error) {
                    console.warn('[YuiGuide] 获取首页交互 API 失败:', error);
                }
            }

            return window.YuiGuideHomeInteractionApi || window.YuiGuidePageHandoff || null;
        },

        async callHomeInteractionApi(methodName, args, fallback) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api[methodName] === 'function') {
                try {
                    const apiTimeoutMs = methodName === 'openPageWithHandoff' ? 6000 : 4200;
                    const apiResult = await resolveWithTimeout(
                        api[methodName].apply(api, Array.isArray(args) ? args : []),
                        apiTimeoutMs,
                        false,
                        'home api ' + methodName
                    );
                    if (apiResult) {
                        return true;
                    }
                    if (typeof fallback === 'function') {
                        return !!(await fallback());
                    }
                    return false;
                } catch (error) {
                    console.warn('[YuiGuide] 首页交互 API 调用失败，回退到本地实现:', methodName, error);
                }
            }

            if (typeof fallback === 'function') {
                return !!(await fallback());
            }

            return false;
        },

        getManagedPanelElement(panelId) {
            if (!panelId) {
                return null;
            }

            return document.getElementById(this.resolveModelPrefix() + '-popup-' + panelId);
        },

        isManagedPanelVisible(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            return !!(popup && popup.style.display === 'flex' && popup.style.opacity !== '0');
        },

        positionManagedPanelNow(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            const popupUi = window.AvatarPopupUI || null;
            const prefix = this.resolveModelPrefix();
            if (!popup || !popupUi || typeof popupUi.positionPopup !== 'function') {
                return false;
            }

            try {
                const pos = popupUi.positionPopup(popup, {
                    buttonId: panelId,
                    buttonPrefix: prefix + '-btn-',
                    triggerPrefix: prefix + '-trigger-icon-',
                    rightMargin: 20,
                    bottomMargin: 60,
                    topMargin: 8,
                    gap: 8,
                    sidePanelWidth: (panelId === 'settings' || panelId === 'agent') ? 320 : 0
                });
                popup.dataset.opensLeft = String(!!(pos && pos.opensLeft));
                return true;
            } catch (error) {
                console.warn('[YuiGuide] positionManagedPanelNow 失败:', panelId, error);
                return false;
            }
        },

        async waitForManagedPanelPositioned(panelId, timeoutMs) {
            const popup = this.getManagedPanelElement(panelId);
            if (!popup) {
                return false;
            }

            const positioned = await this.waitForElement(() => {
                if (
                    popup.style.display === 'flex'
                    && !popup.classList.contains('is-positioning')
                    && typeof popup.dataset.opensLeft === 'string'
                    && popup.dataset.opensLeft !== ''
                ) {
                    return popup;
                }
                return null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 1100);

            if (positioned) {
                this.positionManagedPanelNow(panelId);
                return true;
            }

            return this.positionManagedPanelNow(panelId);
        },

        forceHideManagedPanel(panelId) {
            const popup = this.getManagedPanelElement(panelId);
            if (!popup) {
                return false;
            }

            popup.style.transition = 'none';
            popup.style.opacity = '0';
            popup.style.display = 'none';
            popup.style.pointerEvents = 'none';
            popup.style.transition = '';
            return true;
        },

        getFallbackFloatingButton(buttonId) {
            if (!buttonId) {
                return null;
            }

            return this.resolveElement('#${p}-btn-' + buttonId);
        },

        async setFallbackFloatingPopupVisible(buttonId, visible) {
            const desiredVisible = !!visible;
            if (this.isManagedPanelVisible(buttonId) === desiredVisible) {
                return !desiredVisible || await this.waitForManagedPanelPositioned(buttonId);
            }

            const button = this.getFallbackFloatingButton(buttonId);
            if (!button || typeof button.click !== 'function') {
                return this.isManagedPanelVisible(buttonId) === desiredVisible;
            }

            button.click();

            const result = await this.waitForElement(() => {
                const popup = this.getManagedPanelElement(buttonId);
                const isVisible = this.isManagedPanelVisible(buttonId);
                return isVisible === desiredVisible ? (popup || button) : null;
            }, 1200);

            if (!(!!result && this.isManagedPanelVisible(buttonId) === desiredVisible)) {
                return false;
            }

            return !desiredVisible || await this.waitForManagedPanelPositioned(buttonId);
        },

        async openAgentPanel() {
            return this.callHomeInteractionApi('openAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', true);
            });
        },

        async closeAgentPanel() {
            const closed = await this.callHomeInteractionApi('closeAgentPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('agent', false);
            });
            this.collapseAgentSidePanel('agent-user-plugin');
            this.collapseAgentSidePanel('agent-openclaw');
            return closed;
        },

        async ensureAgentToggleChecked(toggleId, checked) {
            return this.callHomeInteractionApi('ensureAgentToggleChecked', [toggleId, checked], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const checkbox = await this.waitForElement(() => {
                    const input = this.getAgentToggleCheckbox(toggleId);
                    return input && !input.disabled ? input : null;
                }, 5000);
                const toggleItem = this.getAgentToggleElement(toggleId);
                if (!checkbox || !toggleItem) {
                    return false;
                }

                const desiredChecked = checked !== false;
                if (!!checkbox.checked === desiredChecked) {
                    return true;
                }

                toggleItem.click();
                const result = await this.waitForElement(() => {
                    return !!checkbox.checked === desiredChecked ? checkbox : null;
                }, 1500);
                return !!result;
            });
        },

        async ensureAgentSidePanelVisible(toggleId) {
            return this.callHomeInteractionApi('ensureAgentSidePanelVisible', [toggleId], async () => {
                const panelReady = await this.openAgentPanel();
                if (!panelReady) {
                    return false;
                }

                const toggleItem = this.getAgentToggleElement(toggleId);
                const sidePanel = this.getAgentSidePanel(toggleId);
                if (!toggleItem || !sidePanel) {
                    return false;
                }

                this.collapseAvatarFloatingSidePanelsExcept(sidePanel);
                if (typeof sidePanel._expand === 'function') {
                    if (sidePanel._hoverCollapseTimer) {
                        window.clearTimeout(sidePanel._hoverCollapseTimer);
                        sidePanel._hoverCollapseTimer = null;
                    }
                    sidePanel._expand();
                } else {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                }

                try {
                    toggleItem.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                    sidePanel.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                } catch (_) {}

                const result = await this.waitForElement(() => {
                    return this.isAgentSidePanelVisible(toggleId) ? sidePanel : null;
                }, 1500);
                return !!result;
            });
        },

        async waitForAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const sidePanelReady = await this.ensureAgentSidePanelVisible(toggleId);
            if (!sidePanelReady) {
                return null;
            }

            await this.waitForAgentSidePanelLayoutStable(toggleId, 620);

            return this.waitForVisibleElement(() => {
                const button = this.getAgentSidePanelButton(toggleId, actionId);
                if (!button || !this.isAgentSidePanelVisible(toggleId)) {
                    return null;
                }
                return button;
            }, normalizedTimeoutMs);
        },

        async ensureAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;
            const api = this.getHomeInteractionApi();
            if (api && typeof api.ensureAgentSidePanelActionVisible === 'function') {
                try {
                    const actionElement = await resolveWithTimeout(
                        api.ensureAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs),
                        normalizedTimeoutMs + 900,
                        null,
                        'ensureAgentSidePanelActionVisible'
                    );
                    if (actionElement) {
                        await this.waitForAgentSidePanelLayoutStable(toggleId, 620);
                    }
                    if (actionElement) {
                        return actionElement;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] ensureAgentSidePanelActionVisible 调用失败，改用本地兜底:', error);
                }
            }

            return this.waitForAgentSidePanelActionVisible(toggleId, actionId, normalizedTimeoutMs);
        },

        async waitForAgentToggleState(toggleId, checked, timeoutMs) {
            const desiredChecked = checked !== false;
            return this.waitForElement(() => {
                const checkbox = this.getAgentToggleCheckbox(toggleId);
                if (!checkbox) {
                    return null;
                }
                return !!checkbox.checked === desiredChecked ? checkbox : null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 1800);
        },

        readAgentToggleChecked(toggleId) {
            const checkbox = this.getAgentToggleCheckbox(toggleId);
            return checkbox && typeof checkbox.checked === 'boolean'
                ? !!checkbox.checked
                : null;
        },

        async getAgentSwitchSnapshot() {
            const fallbackSnapshot = {
                agentMaster: this.readAgentToggleChecked('agent-master'),
                keyboardControl: this.readAgentToggleChecked('agent-keyboard'),
                userPlugin: this.readAgentToggleChecked('agent-user-plugin')
            };
            const controller = typeof AbortController === 'function'
                ? new AbortController()
                : null;
            const timeoutId = controller
                ? window.setTimeout(() => controller.abort(), 800)
                : 0;

            try {
                const response = await fetch('/api/agent/flags', {
                    signal: controller ? controller.signal : undefined
                });
                if (!response.ok) {
                    return fallbackSnapshot;
                }

                const data = await response.json();
                if (!data || data.success === false) {
                    return fallbackSnapshot;
                }

                const flags = data.agent_flags && typeof data.agent_flags === 'object'
                    ? data.agent_flags
                    : {};
                return {
                    agentMaster: typeof data.analyzer_enabled === 'boolean'
                        ? data.analyzer_enabled
                        : (typeof flags.agent_enabled === 'boolean' ? flags.agent_enabled : fallbackSnapshot.agentMaster),
                    keyboardControl: typeof flags.computer_use_enabled === 'boolean'
                        ? flags.computer_use_enabled
                        : fallbackSnapshot.keyboardControl,
                    userPlugin: typeof flags.user_plugin_enabled === 'boolean'
                        ? flags.user_plugin_enabled
                        : fallbackSnapshot.userPlugin
                };
            } catch (_) {
                return fallbackSnapshot;
            } finally {
                if (timeoutId) {
                    window.clearTimeout(timeoutId);
                }
            }
        },

        async captureDay1TakeoverAgentSwitches() {
            if (this.takeoverOriginalAgentSwitches) {
                return this.takeoverOriginalAgentSwitches;
            }
            const snapshot = await this.getAgentSwitchSnapshot();
            this.takeoverOriginalAgentSwitches = snapshot || {
                agentMaster: null,
                keyboardControl: null,
                userPlugin: null
            };
            return this.takeoverOriginalAgentSwitches;
        },

        async restoreDay1TakeoverAgentSwitches(reason) {
            const snapshot = this.takeoverOriginalAgentSwitches;
            if (!snapshot) {
                return true;
            }
            if (this.takeoverAgentSwitchRestorePromise) {
                return this.takeoverAgentSwitchRestorePromise;
            }

            this.takeoverAgentSwitchRestorePromise = (async () => {
                const originalAgentMaster = typeof snapshot.agentMaster === 'boolean'
                    ? snapshot.agentMaster
                    : null;
                const originalKeyboardControl = typeof snapshot.keyboardControl === 'boolean'
                    ? snapshot.keyboardControl
                    : null;
                let restored = true;

                try {
                    if (originalAgentMaster === true) {
                        restored = (await this.setAgentMasterEnabled(true)) && restored;
                    }
                    if (typeof originalKeyboardControl === 'boolean') {
                        restored = (await this.setAgentFlagEnabled('computer_use_enabled', originalKeyboardControl)) && restored;
                    }
                    if (originalAgentMaster === false) {
                        restored = (await this.setAgentMasterEnabled(false)) && restored;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 恢复 Day1 接管前 Agent 开关失败:', reason || 'restore', error);
                    restored = false;
                }

                if (restored) {
                    this.takeoverOriginalAgentSwitches = null;
                }
                return restored;
            })();

            try {
                return await this.takeoverAgentSwitchRestorePromise;
            } finally {
                if (this.takeoverAgentSwitchRestorePromise) {
                    this.takeoverAgentSwitchRestorePromise = null;
                }
            }
        },

        async clickAgentSidePanelAction(toggleId, actionId, options) {
            const fallbackClick = async () => {
                const button = await this.waitForAgentSidePanelActionVisible(toggleId, actionId, 1800);
                if (!button || typeof button.click !== 'function') {
                    return false;
                }

                button.click();
                return true;
            };

            if (toggleId === 'agent-user-plugin' && actionId === 'management-panel') {
                const api = this.getHomeInteractionApi();
                if (api && typeof api.clickAgentSidePanelAction === 'function') {
                    try {
                        const clicked = await resolveWithTimeout(
                            api.clickAgentSidePanelAction(toggleId, actionId, options || null),
                            2600,
                            false,
                            'clickAgentSidePanelAction'
                        );
                        if (clicked) {
                            return true;
                        }
                        return fallbackClick();
                    } catch (error) {
                        console.warn('[YuiGuide] 插件管理面板 API 点击失败，回退到本地实现:', error);
                    }
                }
                return fallbackClick();
            }

            return this.callHomeInteractionApi(
                'clickAgentSidePanelAction',
                [toggleId, actionId, options || null],
                fallbackClick
            );
        },

        async openSettingsPanel() {
            return this.callHomeInteractionApi('openSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', true);
            });
        },

        async closeSettingsPanel() {
            return this.callHomeInteractionApi('closeSettingsPanel', [], () => {
                return this.setFallbackFloatingPopupVisible('settings', false);
            });
        },

        normalizeSettingsMenuId(menuId) {
            const normalized = typeof menuId === 'string'
                ? menuId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
                : '';
            return normalized || '';
        },

        getSettingsMenuSelector(menuId) {
            const normalizedMenuId = this.normalizeSettingsMenuId(menuId);
            if (!normalizedMenuId) {
                return '';
            }

            return '#' + this.resolveModelPrefix() + '-menu-' + normalizedMenuId;
        },

        getSettingsMenuElement(menuId) {
            const selector = this.getSettingsMenuSelector(menuId);
            if (!selector) {
                return null;
            }

            return this.resolveElement(selector);
        },

        async ensureSettingsMenuVisible(menuId) {
            return this.callHomeInteractionApi('ensureSettingsMenuVisible', [menuId], async () => {
                const panelReady = await this.openSettingsPanel();
                if (!panelReady) {
                    return false;
                }

                if (!menuId) {
                    return true;
                }

                this.collapseCharacterSettingsSidePanel();

                const selector = this.getSettingsMenuSelector(menuId);
                if (!selector) {
                    return false;
                }

                const menuLabel = await this.waitForElement(() => this.resolveElement(selector), 1200);
                if (!menuLabel) {
                    return false;
                }

                const menuItem = menuLabel.closest('.' + this.resolveModelPrefix() + '-settings-menu-item') || menuLabel.parentElement;
                if (menuItem && typeof menuItem.scrollIntoView === 'function') {
                    try {
                        menuItem.scrollIntoView({
                            behavior: 'smooth',
                            block: 'nearest',
                            inline: 'nearest'
                        });
                    } catch (_) {
                        menuItem.scrollIntoView();
                    }
                }

                return true;
            });
        },

        async closeManagedPanels() {
            const results = await Promise.all([
                this.closeAgentPanel(),
                this.closeSettingsPanel()
            ]);

            return results.every(Boolean);
        },

        async openPageWithHandoff(stepId, step) {
            const navigation = step && step.navigation ? step.navigation : null;
            if (!navigation || !navigation.openUrl || !navigation.windowName) {
                return false;
            }

            const targetPage = navigation.targetPage || navigation.windowName || stepId || '';
            const resumeScene = navigation.resumeScene || null;

            return this.callHomeInteractionApi('openPageWithHandoff', [
                targetPage,
                resumeScene,
                navigation.openUrl,
                navigation.windowName,
                navigation.features || ''
            ], async () => {
                const api = this.getHomeInteractionApi();
                if (targetPage === 'plugin_dashboard' && api && typeof api.openPluginDashboard === 'function') {
                    const childWin = await resolveWithTimeout(
                        api.openPluginDashboard(),
                        3600,
                        null,
                        'openPluginDashboard fallback'
                    );
                    return !!childWin;
                }
                if (api && typeof api.openPage === 'function') {
                    const childWin = await resolveWithTimeout(
                        api.openPage(
                            navigation.openUrl,
                            navigation.windowName,
                            navigation.features || ''
                        ),
                        3600,
                        null,
                        'openPage fallback'
                    );
                    return !!childWin;
                }

                return false;
            });
        },

        async waitForOpenedWindow(windowName, timeoutMs) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.waitForWindowOpen === 'function') {
                try {
                    const apiTimeoutMs = Math.max(1000, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 6000) + 800);
                    const openedWindow = await resolveWithTimeout(
                        api.waitForWindowOpen(windowName, timeoutMs),
                        apiTimeoutMs,
                        null,
                        'waitForWindowOpen'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 等待子窗口打开失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            return this.waitForElement(() => {
                if (!normalizedName) {
                    return null;
                }

                const tracked = window._openedWindows && window._openedWindows[normalizedName];
                return tracked && !tracked.closed ? tracked : null;
            }, timeoutMs || 6000);
        },

        async closeNamedWindow(windowName) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.closeWindow === 'function') {
                try {
                    const apiClosed = !!(await resolveWithTimeout(
                        api.closeWindow(windowName),
                        2200,
                        false,
                        'closeWindow'
                    ));
                    if (apiClosed) {
                        return true;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 关闭子窗口失败，改用本地兜底:', error);
                }
            }

            const normalizedName = api && typeof api.normalizeWindowName === 'function'
                ? api.normalizeWindowName(windowName)
                : String(windowName || '');
            const target = normalizedName && window._openedWindows
                ? window._openedWindows[normalizedName]
                : null;
            if (!target) {
                return true;
            }

            try {
                target.close();
                delete window._openedWindows[normalizedName];
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 本地关闭子窗口失败:', error);
                return false;
            }
        },

        async closePluginDashboardWindowIfCreatedByGuide(context) {
            if (!this.pluginDashboardWindowCreatedByGuide) {
                return true;
            }

            try {
                const closed = await this.closeNamedWindow(PLUGIN_DASHBOARD_WINDOW_NAME);
                if (closed) {
                    this.pluginDashboardWindowCreatedByGuide = false;
                    return true;
                }
                console.warn('[YuiGuide] ' + (context || '清理') + '时关闭插件面板失败');
                return false;
            } catch (error) {
                console.warn('[YuiGuide] ' + (context || '清理') + '时关闭插件面板失败:', error);
                return false;
            }
        },

        async setAgentMasterEnabled(enabled) {
            return this.callHomeInteractionApi('setAgentMasterEnabled', [enabled], async () => {
                try {
                    const response = await fetchWithTimeout('/api/agent/command', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                            command: 'set_agent_enabled',
                            enabled: !!enabled
                        })
                    }, 3600);
                    if (!response.ok) {
                        return false;
                    }

                    const data = await response.json();
                    return !!(data && data.success === true);
                } catch (error) {
                    console.warn('[YuiGuide] 设置 Agent 总开关超时或失败:', error);
                    return false;
                }
            });
        },

        async setAgentFlagEnabled(flagKey, enabled) {
            return this.callHomeInteractionApi('setAgentFlagEnabled', [flagKey, enabled], async () => {
                try {
                    const response = await fetchWithTimeout('/api/agent/command', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            request_id: Date.now() + '-' + Math.random().toString(36).slice(2, 8),
                            command: 'set_flag',
                            key: flagKey,
                            value: !!enabled
                        })
                    }, 3600);
                    if (!response.ok) {
                        return false;
                    }

                    const data = await response.json();
                    return !!(data && data.success === true);
                } catch (error) {
                    console.warn('[YuiGuide] 设置 Agent 标志超时或失败:', flagKey, error);
                    return false;
                }
            });
        },

        async openPluginDashboardWindow(options) {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.openPluginDashboard === 'function') {
                try {
                    const openedWindow = await resolveWithTimeout(
                        api.openPluginDashboard(options || null),
                        3600,
                        null,
                        'openPluginDashboard'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] openPluginDashboard 失败，改用本地兜底:', error);
                }
            }

            if (api && typeof api.openPage === 'function') {
                try {
                    const fallbackUrl = new URL('/api/agent/user_plugin/dashboard', window.location.origin);
                    if (window.location && window.location.origin) {
                        fallbackUrl.searchParams.set('yui_opener_origin', window.location.origin);
                    }
                    return await resolveWithTimeout(
                        api.openPage(
                            fallbackUrl.toString(),
                            'plugin_dashboard',
                            '',
                            options || null
                        ),
                        3600,
                        null,
                        'openPage(plugin_dashboard)'
                    );
                } catch (error) {
                    console.warn('[YuiGuide] openPage(plugin_dashboard) 失败:', error);
                }
            }

            return null;
        },

        async waitForManualPluginDashboardOpen(managementButton, spotlightTarget, runId, timeoutMs, guideOpenTriggeredBeforePrompt) {
            if (!managementButton || runId !== this.sceneRunId || this.isStopping()) {
                return {
                    window: null,
                    createdByGuide: false
                };
            }

            const normalizedTimeoutMs = clamp(
                Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 18000),
                6000,
                30000
            );
            const target = spotlightTarget || managementButton;
            this.manualPluginDashboardOpenAllowed = true;
            this.manualPluginDashboardOpenTarget = managementButton;
            this.manualPluginDashboardOpenUserClicked = false;
            const shouldRestoreTutorialInputShield = !!(
                this.overlay
                && this.overlay.tutorialInputShieldActive === true
            );
            if (this.overlay && typeof this.overlay.setInteractionShieldSuppressed === 'function') {
                this.overlay.setInteractionShieldSuppressed(true);
            }
            if (this.overlay && typeof this.overlay.setTutorialInputShieldActive === 'function') {
                this.overlay.setTutorialInputShieldActive(false);
            }
            this.recordExperienceMetric('plugin_dashboard_popup_blocked_prompt', {
                targetPage: 'plugin_dashboard'
            });

            try {
                this.suppressUserCursorReveal();
                this.overlay.activateSpotlight(target);
                this.cursor.wobble();
                const targetRect = this.getElementRect(target) || this.getElementRect(managementButton);
                const promptText = this.resolveGuideCopy(
                    PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY,
                    PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT
                );
                this.showGuideBubble(promptText, {
                    anchorRect: targetRect || null,
                    emotion: 'surprised',
                    bubbleVariant: 'plugin-manual-open'
                }, this.currentSceneId || 'plugin_dashboard_manual_open');

                const openedWindow = await this.waitForOpenedWindow(
                    PLUGIN_DASHBOARD_WINDOW_NAME,
                    normalizedTimeoutMs
                );
                if (openedWindow && !openedWindow.closed) {
                    // If the popup was opened by the user clicking the highlighted tutorial
                    // target, it still belongs to this tutorial step and should be closed
                    // after the dashboard preview. Pre-existing dashboard windows are
                    // handled before this manual prompt path and remain user-owned.
                    const createdByGuide = !!(
                        this.manualPluginDashboardOpenUserClicked
                        || (
                            guideOpenTriggeredBeforePrompt
                            && !this.manualPluginDashboardOpenUserClicked
                        )
                    );
                    this.recordExperienceMetric('plugin_dashboard_popup_manual_opened', {
                        targetPage: 'plugin_dashboard',
                        createdByGuide: createdByGuide
                    });
                    return {
                        window: openedWindow,
                        createdByGuide: createdByGuide
                    };
                }

                this.recordExperienceMetric('plugin_dashboard_popup_manual_open_timeout', {
                    targetPage: 'plugin_dashboard'
                });
                return {
                    window: null,
                    createdByGuide: false
                };
            } finally {
                this.manualPluginDashboardOpenAllowed = false;
                this.manualPluginDashboardOpenTarget = null;
                this.manualPluginDashboardOpenUserClicked = false;
                if (this.overlay && typeof this.overlay.setTutorialInputShieldActive === 'function') {
                    this.overlay.setTutorialInputShieldActive(
                        shouldRestoreTutorialInputShield && runId === this.sceneRunId && !this.isStopping()
                    );
                }
                if (this.overlay && typeof this.overlay.setInteractionShieldSuppressed === 'function') {
                    this.overlay.setInteractionShieldSuppressed(false);
                }
                if (runId === this.sceneRunId && !this.isStopping()) {
                    this.overlay.hideBubble();
                }
            }
        },

        getPluginDashboardExpectedOrigin() {
            const api = this.getHomeInteractionApi();
            if (api && typeof api.getPluginDashboardExpectedOrigin === 'function') {
                try {
                    const apiOrigin = api.getPluginDashboardExpectedOrigin();
                    if (typeof apiOrigin === 'string' && apiOrigin.trim() !== '') {
                        const trimmedOrigin = apiOrigin.trim();
                        try {
                            return new URL(trimmedOrigin).origin;
                        } catch (_) {}
                    }
                } catch (error) {
                    console.warn('[YuiGuide] 获取插件面板 origin 失败:', error);
                }
            }
            if (window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN) {
                try {
                    return new URL(String(window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN), window.location.href).origin;
                } catch (_) {}
            }
            if (window.NEKO_USER_PLUGIN_BASE) {
                try {
                    return new URL(String(window.NEKO_USER_PLUGIN_BASE), window.location.href).origin;
                } catch (_) {}
            }
            return 'http://127.0.0.1:48916';
        },

        isTrustedPluginDashboardOrigin(origin) {
            if (typeof origin !== 'string' || origin.trim() === '') {
                return false;
            }
            try {
                const url = new URL(origin);
                const hostname = String(url.hostname || '').toLowerCase();
                return (
                    (url.protocol === 'http:' || url.protocol === 'https:')
                    && (
                        hostname === '127.0.0.1'
                        || hostname === 'localhost'
                        || hostname === '::1'
                    )
                );
            } catch (_) {
                return false;
            }
        },

        async openModelManagerPage(lanlanName) {
            const api = this.getHomeInteractionApi();
            const targetLanlanName = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            if (api && typeof api.openModelManagerPage === 'function') {
                try {
                    const openedWindow = await resolveWithTimeout(
                        api.openModelManagerPage(targetLanlanName),
                        3600,
                        null,
                        'openModelManagerPage'
                    );
                    if (openedWindow && !openedWindow.closed) {
                        return openedWindow;
                    }
                } catch (error) {
                    console.warn('[YuiGuide] openModelManagerPage 失败，改用本地兜底:', error);
                }
            }

            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            const windowName = this.getModelManagerWindowName(targetLanlanName, appearanceMenuId);
            if (api && typeof api.openPage === 'function') {
                try {
                    return await resolveWithTimeout(
                        api.openPage(
                            '/model_manager?lanlan_name=' + encodeURIComponent(targetLanlanName),
                            windowName
                        ),
                        3600,
                        null,
                        'openPage(model_manager)'
                    );
                } catch (error) {
                    console.warn('[YuiGuide] openPage(model_manager) 失败:', error);
                }
            }

            return null;
        },

        async performCaptureCursorPrelude(durationMs) {
            const totalDurationMs = Number.isFinite(durationMs) ? Math.max(600, durationMs) : 2000;
            const origin = this.cursor.hasPosition()
                ? this.overlay.getCursorPosition()
                : this.getDefaultCursorOrigin();
            if (!origin) {
                return;
            }

            if (!this.cursor.hasPosition()) {
                this.cursor.showAt(origin.x, origin.y);
                if (!(await this.waitForSceneDelay(120))) {
                    return;
                }
            }

            const points = [
                { x: origin.x - 60, y: origin.y - 36 },
                { x: origin.x + 54, y: origin.y - 24 },
                { x: origin.x + 42, y: origin.y + 48 },
                { x: origin.x - 48, y: origin.y + 36 },
                { x: origin.x, y: origin.y }
            ];
            const segmentDurationMs = Math.max(180, Math.round(totalDurationMs / points.length));

            for (let index = 0; index < points.length; index += 1) {
                const point = points[index];
                const moved = await this.cursor.moveToPoint(point.x, point.y, {
                    durationMs: segmentDurationMs,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (!moved && this.isStopping()) {
                    return;
                }
                if (!moved) {
                    if (!this.scenePausedForResistance) {
                        if (this.isCursorTransientMotionActive()) {
                            await this.waitForCursorTransientMotion();
                            index -= 1;
                            continue;
                        }
                        return;
                    }
                    await this.waitUntilSceneResumed();
                    index -= 1;
                    continue;
                }
                if (this.scenePausedForResistance) {
                    await this.waitUntilSceneResumed();
                }
                if (this.destroyed || this.angryExitTriggered) {
                    return;
                }
                this.cursor.wobble();
                if (!(await this.waitForSceneDelay(60))) {
                    return;
                }
            }
        },

        resolveCursorPointFromRect(rect, options) {
            if (!rect) {
                return null;
            }
            const normalizedOptions = options || {};
            const point = {
                x: rect.left + (rect.width / 2),
                y: rect.top + (rect.height / 2)
            };
            const offset = normalizedOptions.targetPointOffset || normalizedOptions.pointOffset || null;
            if (offset) {
                if (Number.isFinite(offset.x)) {
                    point.x += offset.x;
                }
                if (Number.isFinite(offset.y)) {
                    point.y += offset.y;
                }
            }
            if (normalizedOptions.clampTargetPointToRect === true) {
                const inset = Number.isFinite(normalizedOptions.targetPointClampInsetPx)
                    ? Math.max(0, normalizedOptions.targetPointClampInsetPx)
                    : 0;
                point.x = clamp(point.x, rect.left + inset, rect.right - inset);
                point.y = clamp(point.y, rect.top + inset, rect.bottom - inset);
            }
            return point;
        },

        async moveCursorToElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            this.releaseExternalizedChatCursorToHome();
            while (!this.isStopping()) {
                await this.waitUntilSceneResumed();
                const rect = this.getElementRect(element);
                if (!rect) {
                    return false;
                }

                const usesAdjustedPoint = !!(
                    normalizedOptions.targetPointOffset
                    || normalizedOptions.pointOffset
                    || normalizedOptions.clampTargetPointToRect === true
                );
                const point = usesAdjustedPoint
                    ? this.resolveCursorPointFromRect(rect, normalizedOptions)
                    : null;
                const moveOptions = {
                    durationMs: Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS,
                    exactDuration: normalizedOptions.exactDuration === true,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                };
                const moved = point
                    ? await this.cursor.moveToPoint(point.x, point.y, moveOptions)
                    : await this.cursor.moveToRect(rect, moveOptions);
                if (moved) {
                    if (point) {
                        this.rememberAvatarFloatingSceneCursorAnchorPoint(this.currentSceneId, point);
                    } else {
                        this.rememberAvatarFloatingSceneCursorAnchor(this.currentSceneId, element);
                    }
                    return true;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
            }

            return false;
        },

        async resolveElementCenterPoint(element, timeoutMs, options) {
            const normalizedOptions = options || {};
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 800;
            const startedAt = Date.now();
            let pausedAt = 0;
            let pausedTotalMs = 0;

            while ((Date.now() - startedAt - pausedTotalMs) < normalizedTimeoutMs) {
                if (this.destroyed || this.angryExitTriggered) {
                    return null;
                }

                const now = Date.now();
                if (this.scenePausedForResistance) {
                    if (!pausedAt) {
                        pausedAt = now;
                    }
                    await wait(80);
                    continue;
                }

                if (pausedAt) {
                    pausedTotalMs += Math.max(0, now - pausedAt);
                    pausedAt = 0;
                }

                const rect = this.getElementRect(element);
                if (rect) {
                    return Object.assign(this.resolveCursorPointFromRect(rect, normalizedOptions), {
                        rect: rect
                    });
                }

                await this.waitForSceneDelay(80);
            }

            const finalRect = this.getElementRect(element);
            if (!finalRect) {
                return null;
            }

            return Object.assign(this.resolveCursorPointFromRect(finalRect, normalizedOptions), {
                rect: finalRect
            });
        },

        async moveCursorToTrackedElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            this.releaseExternalizedChatCursorToHome();
            const totalDurationMs = Number.isFinite(durationMs) ? durationMs : DEFAULT_CURSOR_DURATION_MS;
            const exactDuration = normalizedOptions.exactDuration === true;
            const firstLegMs = exactDuration
                ? Math.max(0, Math.round(totalDurationMs * 0.7))
                : Math.max(180, Math.round(totalDurationMs * 0.7));
            const secondLegMs = exactDuration
                ? Math.max(0, totalDurationMs - firstLegMs)
                : Math.max(140, totalDurationMs - firstLegMs);
            const recheckDelayMs = Number.isFinite(normalizedOptions.recheckDelayMs)
                ? normalizedOptions.recheckDelayMs
                : 320;
            const settleDelayMs = Number.isFinite(normalizedOptions.settleDelayMs)
                ? normalizedOptions.settleDelayMs
                : 0;

            const initialPoint = await this.resolveElementCenterPoint(element, 420, normalizedOptions);
            if (!initialPoint) {
                return false;
            }
            while (!this.isStopping()) {
                const movedToInitialPoint = await this.cursor.moveToPoint(initialPoint.x, initialPoint.y, {
                    durationMs: firstLegMs,
                    exactDuration: exactDuration,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (movedToInitialPoint) {
                    break;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
                await this.waitUntilSceneResumed();
            }
            if (this.isStopping()) {
                return false;
            }

            if (settleDelayMs > 0) {
                if (!(await this.waitForSceneDelay(settleDelayMs))) {
                    return false;
                }
            }
            if (recheckDelayMs > 0) {
                if (!(await this.waitForSceneDelay(recheckDelayMs))) {
                    return false;
                }
            }
            if (this.destroyed || this.angryExitTriggered) {
                return false;
            }

            const finalPoint = await this.resolveElementCenterPoint(element, 420, normalizedOptions);
            if (!finalPoint) {
                return false;
            }

            while (!this.isStopping()) {
                const movedToFinalPoint = await this.cursor.moveToPoint(finalPoint.x, finalPoint.y, {
                    durationMs: secondLegMs,
                    exactDuration: exactDuration,
                    pauseCheck: () => this.scenePausedForResistance,
                    cancelCheck: () => this.isStopping()
                });
                if (movedToFinalPoint) {
                    return true;
                }
                if (this.isCursorTransientMotionActive()) {
                    await this.waitForCursorTransientMotion();
                    continue;
                }
                if (!this.scenePausedForResistance) {
                    return false;
                }
                await this.waitUntilSceneResumed();
            }

            return false;
        },

        isCursorAlignedWithElement(element, tolerancePx) {
            const cursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const rect = this.getElementRect(element);
            if (!cursorPosition || !rect) {
                return false;
            }

            const tolerance = Number.isFinite(tolerancePx) ? Math.max(0, tolerancePx) : 6;
            return cursorPosition.x >= rect.left - tolerance
                && cursorPosition.x <= rect.right + tolerance
                && cursorPosition.y >= rect.top - tolerance
                && cursorPosition.y <= rect.bottom + tolerance;
        },

        async realignCursorToAgentSidePanelAction(toggleId, actionId, durationMs) {
            const stablePanel = await this.waitForAgentSidePanelLayoutStable(toggleId, 980);
            if (!stablePanel || this.isStopping()) {
                return false;
            }

            const button = await this.waitForVisibleElement(() => {
                const actionButton = this.getAgentSidePanelButton(toggleId, actionId);
                if (!actionButton || !this.isAgentSidePanelVisible(toggleId)) {
                    return null;
                }
                return this.getElementRect(actionButton) ? actionButton : null;
            }, 900);
            if (!button || this.isStopping()) {
                return false;
            }

            this.clearVirtualSpotlight('plugin-management-entry');
            const spotlightTarget = this.createPluginManagementEntrySpotlight(button) || button;
            this.replaceRetainedExtraSpotlight(
                (candidate) => candidate
                    && (
                        candidate === button
                        || (
                            typeof candidate.getAttribute === 'function'
                            && candidate.getAttribute('data-yui-guide-virtual-spotlight') === 'plugin-management-entry'
                        )
                    ),
                spotlightTarget
            );
            this.overlay.activateSpotlight(spotlightTarget);

            if (this.isCursorAlignedWithElement(button, 5)) {
                return true;
            }

            return this.moveCursorToElement(
                button,
                Number.isFinite(durationMs) ? durationMs : 360
            );
        },

        async clickCursorAndWait(holdMs) {
            const visibleMs = clamp(
                Math.round(Number.isFinite(holdMs) ? holdMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                900
            );
            this.cursor.click(visibleMs);
            return await this.waitForSceneDelay(visibleMs);
        },

        async clickCursorAndWaitExact(holdMs) {
            const visibleMs = clamp(
                Math.round(Number.isFinite(holdMs) ? holdMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                120,
                900
            );
            this.cursor.click(visibleMs);
            return await this.waitForSceneDelay(visibleMs);
        },

        async runActionWithCursorClick(holdMs, action) {
            const clickPromise = this.clickCursorAndWait(holdMs);
            let actionPromise = Promise.resolve(true);
            if (typeof action === 'function') {
                try {
                    actionPromise = Promise.resolve(action());
                } catch (error) {
                    actionPromise = Promise.reject(error);
                }
                actionPromise.catch(() => {});
            }
            const clickCompleted = await clickPromise;
            if (!clickCompleted) {
                return false;
            }
            return await actionPromise;
        },

        async runActionWithCursorClickExact(holdMs, action) {
            const clickPromise = this.clickCursorAndWaitExact(holdMs);
            let actionPromise = Promise.resolve(true);
            if (typeof action === 'function') {
                try {
                    actionPromise = Promise.resolve(action());
                } catch (error) {
                    actionPromise = Promise.reject(error);
                }
                actionPromise.catch(() => {});
            }
            const clickCompleted = await clickPromise;
            if (!clickCompleted) {
                return false;
            }
            return await actionPromise;
        },

        hoverElement(element) {
            if (!element) {
                return;
            }

            try {
                element.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
                element.dispatchEvent(new MouseEvent('mouseover', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            } catch (_) {}
        },

        stopHoverElement(element) {
            if (!element) {
                return;
            }

            try {
                element.dispatchEvent(new MouseEvent('mouseleave', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
                element.dispatchEvent(new MouseEvent('mouseout', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            } catch (_) {}
        },

        getVisibleHomeModelElement() {
            const candidates = [
                document.getElementById('live2d-container'),
                document.getElementById('vrm-container'),
                document.getElementById('mmd-container')
            ];

            for (let index = 0; index < candidates.length; index += 1) {
                const element = candidates[index];
                if (this.isElementVisible(element)) {
                    return element;
                }
            }

            return null;
        },

        async waitForHomeMainUIReady(timeoutMs) {
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 恢复主界面失败:', error);
                }
            }

            return this.waitForElement(() => {
                const settingsButton = this.getFallbackFloatingButton('settings');
                const modelElement = this.getVisibleHomeModelElement();
                if (this.isElementVisible(settingsButton) && modelElement) {
                    return settingsButton;
                }

                return null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 3200);
        },

        async performHighlightedApiClick(options) {
            const normalized = options || {};
            const target = normalized.target || null;
            if (!target) {
                return false;
            }

            this.applyGuideHighlights({
                primary: target,
                secondary: normalized.secondary || null
            });
            const moved = await this.moveCursorToElement(target, normalized.durationMs);
            if (!moved) {
                return false;
            }
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            const clickVisibleMs = clamp(
                Math.round(Number.isFinite(normalized.clickVisibleMs) ? normalized.clickVisibleMs : DEFAULT_CURSOR_CLICK_VISIBLE_MS),
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                900
            );
            const actionResultPromise = this.runActionWithCursorClick(clickVisibleMs, normalized.action);
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }
            const actionResult = await actionResultPromise;
            if (normalized.runId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            return !!actionResult;
        },

        getVoiceControlButtonTarget() {
            return this.getFloatingButtonShell(
                this.getFallbackFloatingButton('mic')
                || this.resolveElement(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.voiceControl))
            );
        },

        async runIntroVoiceControlButtonShowcase(voiceKey, fallbackText) {
            const voiceControlButton = this.getVoiceControlButtonTarget();
            if (!voiceControlButton) {
                return;
            }

            this.setSpotlightGeometryHint(voiceControlButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.overlay.activateSpotlight(voiceControlButton);

            await this.waitForExternalizedChatCursorMove('day1_history_handle', 1800);

            if (!this.cursor.hasPosition()) {
                const historyHandleAnchor = this.getAvatarFloatingSceneCursorAnchor('day1_history_handle');
                if (historyHandleAnchor) {
                    this.cursor.showAt(historyHandleAnchor.x, historyHandleAnchor.y);
                }
            }

            if (!this.cursor.hasPosition() && !this.restoreCursorFromExternalizedChatAnchor(30000)) {
                const introTarget = this.getChatInputTarget() || this.getChatWindowTarget();
                const introRect = this.getElementRect(introTarget);
                if (introRect) {
                    this.cursor.showAt(
                        introRect.left + introRect.width / 2,
                        introRect.top + introRect.height / 2
                    );
                } else {
                    const origin = this.getDefaultCursorOrigin();
                    this.cursor.showAt(origin.x, origin.y);
                }
            }

            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const moveDurationMs = clamp(Math.round(narrationDurationMs * 0.16), 900, 2200);
            await this.moveCursorToElement(voiceControlButton, moveDurationMs);
        },

        async runTakeoverKeyboardControlSequence(step, performance, runId) {
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);
            const createToggleSpotlightTarget = (key, element) => {
                const rect = this.getElementRect(element);
                if (!rect) {
                    return element;
                }

                return this.createVirtualSpotlight(key, {
                    left: Math.max(0, rect.left - 8),
                    top: Math.max(0, rect.top - 4),
                    right: Math.min(window.innerWidth, rect.right + 8),
                    bottom: Math.min(window.innerHeight, rect.bottom + 4)
                }, {
                    padding: 4,
                    radius: 18
                });
            };
            const catPawButton = await this.waitForVisibleTarget([
                () => this.getFloatingButtonShell(this.getFallbackFloatingButton('agent')),
                () => this.getFloatingButtonShell(this.resolveElement((performance && performance.cursorTarget) || '')),
                () => this.getFloatingButtonShell(this.resolveElement(step && step.anchor ? step.anchor : '')),
                () => this.getFloatingButtonShell(this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw)))
            ], 2200);
            if (!catPawButton || guardFailed()) {
                return false;
            }
            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.addRetainedExtraSpotlight(catPawButton);

            const openedAgentPanel = await this.performHighlightedApiClick({
                target: catPawButton,
                durationMs: scaleSceneMs(1500, 900, 2600),
                runId: runId,
                action: () => this.openAgentPanel()
            });
            if (!openedAgentPanel || guardFailed()) {
                return false;
            }

            if (this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile('expressions/xxy.exp3.json');
            }

            const agentMasterToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-master');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 4000);
            if (!agentMasterToggle || guardFailed()) {
                return false;
            }
            const agentMasterSpotlight = createToggleSpotlightTarget('takeover-agent-master-toggle', agentMasterToggle);
            this.addRetainedExtraSpotlight(agentMasterSpotlight);

            const enabledAgentMaster = await this.performHighlightedApiClick({
                target: agentMasterSpotlight,
                durationMs: scaleSceneMs(1200, 760, 2200),
                runId: runId,
                action: async () => {
                    const enabled = await this.setAgentMasterEnabled(true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-master', true, 1800));
                }
            });
            if (!enabledAgentMaster || guardFailed()) {
                return false;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(240, 120, 600))) || guardFailed()) {
                return false;
            }

            const keyboardToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-keyboard');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 2400);
            if (!keyboardToggle || guardFailed()) {
                return false;
            }
            let keyboardToggleSpotlight = null;
            const isKeyboardToggleSpotlight = (candidate) => {
                return !!(
                    candidate === keyboardToggleSpotlight
                    || (
                        candidate
                        && typeof candidate.getAttribute === 'function'
                        && candidate.getAttribute('data-yui-guide-virtual-spotlight') === 'takeover-keyboard-toggle'
                    )
                );
            };
            const refreshKeyboardToggleSpotlight = (options) => {
                const normalizedOptions = options || {};
                const refreshedSpotlight = createToggleSpotlightTarget('takeover-keyboard-toggle', keyboardToggle);
                if (!refreshedSpotlight || guardFailed()) {
                    return null;
                }
                this.replaceRetainedExtraSpotlight(isKeyboardToggleSpotlight, refreshedSpotlight);
                if (normalizedOptions.activate === true) {
                    this.overlay.activateSpotlight(refreshedSpotlight);
                }
                keyboardToggleSpotlight = refreshedSpotlight;
                return refreshedSpotlight;
            };
            await this.waitForStableElementRect(keyboardToggle, scaleSceneMs(320, 160, 760));
            keyboardToggleSpotlight = refreshKeyboardToggleSpotlight({ activate: true });
            if (!keyboardToggleSpotlight || guardFailed()) {
                return false;
            }
            this.removeRetainedExtraSpotlight(agentMasterSpotlight);

            this.applyGuideHighlights({
                primary: keyboardToggleSpotlight
            });
            const movedToKeyboardToggle = await this.moveCursorToTrackedElement(
                keyboardToggle,
                scaleSceneMs(520, 320, 950),
                {
                    recheckDelayMs: scaleSceneMs(180, 80, 420),
                    settleDelayMs: scaleSceneMs(80, 40, 180)
                }
            );
            if (!movedToKeyboardToggle || guardFailed()) {
                return false;
            }

            keyboardToggleSpotlight = refreshKeyboardToggleSpotlight();
            if (!keyboardToggleSpotlight || guardFailed()) {
                return false;
            }
            if (!this.isCursorAlignedWithElement(keyboardToggle, 5)) {
                const realignedToKeyboardToggle = await this.moveCursorToTrackedElement(
                    keyboardToggle,
                    scaleSceneMs(220, 120, 420),
                    {
                        recheckDelayMs: scaleSceneMs(80, 40, 180),
                        settleDelayMs: scaleSceneMs(40, 20, 120)
                    }
                );
                if (!realignedToKeyboardToggle || guardFailed()) {
                    return false;
                }
                keyboardToggleSpotlight = refreshKeyboardToggleSpotlight();
                if (!keyboardToggleSpotlight || guardFailed()) {
                    return false;
                }
            }

            const enabledKeyboardControl = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                async () => {
                    const enabled = await this.setAgentFlagEnabled('computer_use_enabled', true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-keyboard', true, 1800));
                }
            );
            if (!enabledKeyboardControl || guardFailed()) {
                return false;
            }

            await this.waitForStableElementRect(keyboardToggle, scaleSceneMs(320, 160, 760));
            keyboardToggleSpotlight = refreshKeyboardToggleSpotlight();
            if (!keyboardToggleSpotlight || guardFailed()) {
                return false;
            }
            this.rememberAvatarFloatingSceneCursorAnchor('day1_takeover_capture_cursor', keyboardToggleSpotlight);

            const ghostCursorLookAtHandle = await this.startGhostCursorLookAtPerformance({
                isCancelled: () => guardFailed()
            });
            await this.stopIntroVoiceCursorLookAtPerformance(
                    ghostCursorLookAtHandle,
                    'takeover_keyboard_control_complete'
                );
            await this.stopPersistentGhostCursorLookAtPerformance('takeover_top_peek');
            if (guardFailed()) {
                return false;
            }
            const avatarStageApi = window.YuiGuideAvatarStage;
            if (avatarStageApi && typeof avatarStageApi.startPluginDashboardCornerPeek === 'function') {
                try {
                    this.takeoverTopPeekHandle = await avatarStageApi.startPluginDashboardCornerPeek({
                        targetPreset: 'top_flipped',
                        reducedMotion: this.shouldReduceTutorialMotion(),
                        isCancelled: () => runId !== this.sceneRunId || this.isStopping()
                    });
                } catch (error) {
                    console.warn('[YuiGuide] 插件面板角落动作启动失败:', error);
                    this.takeoverTopPeekHandle = null;
                }
            }
            if (guardFailed()) {
                return false;
            }

            if (this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile('expressions/slh.exp3.json');
            }

            await this.waitForSceneDelay(scaleSceneMs(180, 80, 420));
            return !guardFailed();
        },

        async runPluginDashboardLaunchSequence(step, performance, runId) {
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);

            if (!(await this.openAgentPanel()) || guardFailed()) {
                return null;
            }

            const pluginToggle = await this.waitForElement(() => {
                const toggleItem = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggleItem) ? toggleItem : null;
            }, 2200);
            if (!pluginToggle || guardFailed()) {
                return null;
            }

            const enabledUserPlugin = await this.performHighlightedApiClick({
                target: pluginToggle,
                durationMs: scaleSceneMs(1300, 820, 2300),
                runId: runId,
                action: async () => {
                    const enabled = await this.setAgentFlagEnabled('user_plugin_enabled', true);
                    if (!enabled) {
                        return false;
                    }
                    return !!(await this.waitForAgentToggleState('agent-user-plugin', true, 1800));
                }
            });
            if (!enabledUserPlugin || guardFailed()) {
                return null;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(180, 80, 420))) || guardFailed()) {
                return null;
            }

            this.hoverElement(pluginToggle);
            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                2600
            );
            if (!managementButton || guardFailed()) {
                return null;
            }

            const stableManagementButton = await this.waitForStableElementRect(
                managementButton,
                scaleSceneMs(320, 160, 760)
            );
            const managementMovementTarget = stableManagementButton || managementButton;
            if (!managementMovementTarget || guardFailed()) {
                return null;
            }

            this.clearVirtualSpotlight('plugin-management-entry');
            const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;

            this.overlay.activateSpotlight(managementSpotlightTarget);
            if (!(await this.waitForSceneDelay(scaleSceneMs(60, 40, 180))) || guardFailed()) {
                return null;
            }

            const movedToManagementButton = await this.moveCursorToTrackedElement(
                managementMovementTarget,
                scaleSceneMs(1900, 1200, 3200),
                {
                    recheckDelayMs: scaleSceneMs(180, 80, 420)
                }
            );
            if (!movedToManagementButton || guardFailed()) {
                return null;
            }

            if (!(await this.waitForSceneDelay(scaleSceneMs(90, 40, 220))) || guardFailed()) {
                return null;
            }

            const realignedToManagementButton = await this.realignCursorToAgentSidePanelAction(
                'agent-user-plugin',
                'management-panel',
                scaleSceneMs(420, 180, 760)
            );
            if (!realignedToManagementButton || guardFailed()) {
                return null;
            }

            const managementOpenResult = await this.runActionWithCursorClick(scaleSceneMs(180, 90, 420), async () => {
                const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                    keepMainUIVisible: true
                });
                return {
                    existingPluginDashboardWindow,
                    hadPluginDashboard,
                    agentPanelActionOpened
                };
            });
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            const guideTriggeredPluginDashboardOpen = !!agentPanelActionOpened;

            let pluginDashboardWindow = null;
            if (hadPluginDashboard) {
                try {
                    existingPluginDashboardWindow.location.reload();
                    pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                    this.pluginDashboardWindowCreatedByGuide = false;
                } catch (error) {
                    console.warn('[YuiGuide] 刷新已有插件面板失败:', error);
                    pluginDashboardWindow = await this.openPluginDashboardWindow({
                        keepMainUIVisible: true
                    });
                    if (!pluginDashboardWindow || pluginDashboardWindow.closed) {
                        pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                    }
                    this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
                    if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                        try {
                            existingPluginDashboardWindow.close();
                        } catch (closeError) {
                            console.warn('[YuiGuide] 关闭旧插件面板失败:', closeError);
                        }
                    }
                }
            } else if (agentPanelActionOpened) {
                pluginDashboardWindow = await this.waitForOpenedWindow(
                    PLUGIN_DASHBOARD_WINDOW_NAME,
                    scaleSceneMs(1200, 700, 1800)
                );
                this.pluginDashboardWindowCreatedByGuide = !!(
                    guideTriggeredPluginDashboardOpen
                    && pluginDashboardWindow
                    && !pluginDashboardWindow.closed
                );
            }

            if (
                (!pluginDashboardWindow || pluginDashboardWindow.closed)
                && runId === this.sceneRunId
                && !this.destroyed
                && !this.angryExitTriggered
            ) {
                const manualPluginDashboardOpen = await this.waitForManualPluginDashboardOpen(
                    managementButton,
                    managementSpotlightTarget,
                    runId,
                    scaleSceneMs(18000, 9000, 26000),
                    guideTriggeredPluginDashboardOpen
                );
                pluginDashboardWindow = manualPluginDashboardOpen && manualPluginDashboardOpen.window;
                this.pluginDashboardWindowCreatedByGuide = !!(
                    manualPluginDashboardOpen
                    && manualPluginDashboardOpen.createdByGuide
                    && pluginDashboardWindow
                    && !pluginDashboardWindow.closed
                );
            }

            return {
                pluginDashboardWindow: pluginDashboardWindow,
                pluginToggle: pluginToggle,
                managementSpotlightTarget: managementSpotlightTarget
            };
        },

        async runPluginPreviewHomeExitSequence(targets, runId, scaleSceneMs) {
            const normalizedTargets = targets || {};
            const delay = async (value, minValue, maxValue) => {
                const waitMs = typeof scaleSceneMs === 'function'
                    ? scaleSceneMs(value, minValue, maxValue)
                    : value;
                return this.waitForSceneDelay(waitMs);
            };
            const guardFailed = () => runId !== this.sceneRunId || this.isStopping();
            const removeHighlight = async (element) => {
                if (!element || guardFailed()) {
                    return;
                }
                this.removeRetainedExtraSpotlight(element);
                await delay(140, 80, 260);
            };

            await removeHighlight(normalizedTargets.managementButton);
            await removeHighlight(normalizedTargets.pluginToggle);
            await removeHighlight(normalizedTargets.agentMasterToggle);
            if (guardFailed()) {
                return;
            }

            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            await delay(180, 100, 360);
            if (guardFailed()) {
                return;
            }

            await this.closeAgentPanel().catch(() => {});
            await removeHighlight(normalizedTargets.catPawButton);
        },

        async cleanupPluginPreviewState(targets) {
            const normalizedTargets = targets || {};
            this.stopHoverElement(normalizedTargets.hoverTarget || normalizedTargets.pluginToggle || null);
            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            this.overlay.clearActionSpotlight();
            await this.closePluginDashboardWindowIfCreatedByGuide('插件预览中途清理');
            await this.closeAgentPanel().catch(() => {});
        },

        async runTakeoverCaptureActionSequence(step, performance, runId) {
            this.customSecondarySpotlightTarget = null;
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            let shouldCleanupPreviewState = false;
            let pluginPreviewCleanedUp = false;
            let hoveredPluginToggle = null;
            const scaleSceneMs = this.createSceneScaler(performance && performance.voiceKey);
            const guardFailed = () => this.isGuardFailed(runId);

            const catPawButton = await this.waitForVisibleTarget([
                () => this.getFloatingButtonShell(this.getFallbackFloatingButton('agent')),
                () => this.getFloatingButtonShell(this.resolveElement((performance && performance.cursorTarget) || '')),
                () => this.getFloatingButtonShell(this.resolveElement(step && step.anchor ? step.anchor : '')),
                () => this.getFloatingButtonShell(this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw)))
            ], 2200);
            if (!catPawButton || guardFailed()) {
                return null;
            }
            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });

            try {
                // 1-3. 高亮猫爪 -> 平滑移动 -> 点击并打开猫爪面板
                shouldCleanupPreviewState = true;
                this.addRetainedExtraSpotlight(catPawButton);
                this.overlay.clearActionSpotlight();
                const movedToCatPaw = await this.moveCursorToElement(catPawButton, scaleSceneMs(1500, 900, 2600));
                if (!movedToCatPaw || guardFailed()) {
                    return null;
                }

                const agentPanelOpened = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.openAgentPanel()
                );
                if (!agentPanelOpened || guardFailed()) {
                    return null;
                }

                const agentMasterToggle = await this.waitForElement(() => {
                    const toggleItem = this.getAgentToggleElement('agent-master');
                    return this.getElementRect(toggleItem) ? toggleItem : null;
                }, 4000);
                if (!agentMasterToggle || guardFailed()) {
                    return null;
                }

                // 4-6. 高亮猫爪总开关 -> 平滑移动 -> 点击并同步打开
                this.addRetainedExtraSpotlight(agentMasterToggle);
                const movedToAgentMaster = await this.moveCursorToElement(agentMasterToggle, scaleSceneMs(1200, 760, 2200));
                if (!movedToAgentMaster || guardFailed()) {
                    return null;
                }

                const agentMasterEnabled = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.setAgentMasterEnabled(true)
                );
                if (!agentMasterEnabled || guardFailed()) {
                    return null;
                }

                const agentMasterState = await this.waitForAgentToggleState('agent-master', true, 1800);
                if (!agentMasterState || guardFailed()) {
                    return null;
                }
                if (!(await this.waitForSceneDelay(scaleSceneMs(420, 180, 900)))) {
                    return null;
                }
                if (guardFailed()) {
                    return null;
                }

                const pluginToggle = await this.waitForElement(() => {
                    const toggleItem = this.getAgentToggleElement('agent-user-plugin');
                    return this.getElementRect(toggleItem) ? toggleItem : null;
                }, 2200);
                if (!pluginToggle || guardFailed()) {
                    return null;
                }

                // 7-9. 高亮用户插件 -> 平滑移动 -> 点击并同步打开
                this.addRetainedExtraSpotlight(pluginToggle);
                const movedToPluginToggle = await this.moveCursorToElement(pluginToggle, scaleSceneMs(1300, 820, 2300));
                if (!movedToPluginToggle || guardFailed()) {
                    return null;
                }

                const pluginToggleEnabled = await this.runActionWithCursorClick(
                    scaleSceneMs(420, 240, 900),
                    () => this.setAgentFlagEnabled('user_plugin_enabled', true)
                );
                if (!pluginToggleEnabled || guardFailed()) {
                    return null;
                }

                const pluginToggleState = await this.waitForAgentToggleState('agent-user-plugin', true, 1800);
                if (!pluginToggleState || guardFailed()) {
                    return null;
                }

                if (!(await this.waitForSceneDelay(scaleSceneMs(180, 80, 420)))) {
                    return null;
                }

                // 10. 通过悬停让管理面板显现
                hoveredPluginToggle = pluginToggle;
                this.hoverElement(pluginToggle);

                const managementButton = await this.ensureAgentSidePanelActionVisible(
                    'agent-user-plugin',
                    'management-panel',
                    2600
                );
                if (!managementButton || guardFailed()) {
                    return null;
                }

                const stableManagementButton = await this.waitForStableElementRect(
                    managementButton,
                    scaleSceneMs(320, 160, 760)
                );
                const managementMovementTarget = stableManagementButton || managementButton;
                if (!managementMovementTarget || guardFailed()) {
                    return null;
                }
                this.clearVirtualSpotlight('plugin-management-entry');
                const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;

                // 11-13. 高亮管理面板 -> 移动到高亮中心点 -> 点击并同步打开真实页面
                this.addRetainedExtraSpotlight(managementSpotlightTarget);
                if (!(await this.waitForSceneDelay(scaleSceneMs(60, 40, 180)))) {
                    return null;
                }
                const movedToManagementButton = await this.moveCursorToTrackedElement(
                    managementMovementTarget,
                    scaleSceneMs(1900, 1200, 3200),
                    {
                        recheckDelayMs: scaleSceneMs(180, 80, 420)
                    }
                );
                if (!movedToManagementButton || guardFailed()) {
                    return null;
                }

                if (!(await this.waitForSceneDelay(scaleSceneMs(90, 40, 220)))) {
                    return null;
                }
                const realignedToManagementButton = await this.realignCursorToAgentSidePanelAction(
                    'agent-user-plugin',
                    'management-panel',
                    scaleSceneMs(420, 180, 760)
                );
                if (!realignedToManagementButton || guardFailed()) {
                    return null;
                }
                const managementOpenResult = await this.runActionWithCursorClick(scaleSceneMs(180, 90, 420), async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                });
                const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
                const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
                const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
                const guideTriggeredPluginDashboardOpen = !!agentPanelActionOpened;
                let pluginDashboardWindow = null;
                if (hadPluginDashboard) {
                    try {
                        existingPluginDashboardWindow.location.reload();
                        pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                        this.pluginDashboardWindowCreatedByGuide = false;
                    } catch (error) {
                        console.warn('[YuiGuide] 刷新已有插件面板失败:', error);
                        pluginDashboardWindow = await this.openPluginDashboardWindow({
                            keepMainUIVisible: true
                        });
                        if (!pluginDashboardWindow || pluginDashboardWindow.closed) {
                            pluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 6000);
                        }
                        this.pluginDashboardWindowCreatedByGuide = !!(pluginDashboardWindow && !pluginDashboardWindow.closed);
                        if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                            try {
                                existingPluginDashboardWindow.close();
                            } catch (closeError) {
                                console.warn('[YuiGuide] 关闭旧插件面板失败:', closeError);
                            }
                        }
                    }
                } else if (agentPanelActionOpened) {
                    pluginDashboardWindow = await this.waitForOpenedWindow(
                        PLUGIN_DASHBOARD_WINDOW_NAME,
                        scaleSceneMs(1200, 700, 1800)
                    );
                    this.pluginDashboardWindowCreatedByGuide = !!(
                        guideTriggeredPluginDashboardOpen
                        && pluginDashboardWindow
                        && !pluginDashboardWindow.closed
                    );
                }
                if (
                    (!pluginDashboardWindow || pluginDashboardWindow.closed)
                    && runId === this.sceneRunId
                    && !this.destroyed
                    && !this.angryExitTriggered
                ) {
                    const manualPluginDashboardOpen = await this.waitForManualPluginDashboardOpen(
                        managementButton,
                        managementSpotlightTarget,
                        runId,
                        scaleSceneMs(18000, 9000, 26000),
                        guideTriggeredPluginDashboardOpen
                    );
                    pluginDashboardWindow = manualPluginDashboardOpen && manualPluginDashboardOpen.window;
                    this.pluginDashboardWindowCreatedByGuide = !!(
                        manualPluginDashboardOpen
                        && manualPluginDashboardOpen.createdByGuide
                        && pluginDashboardWindow
                        && !pluginDashboardWindow.closed
                    );
                }

                if (pluginDashboardWindow && !pluginDashboardWindow.closed) {
                    await this.runPluginPreviewHomeExitSequence({
                        managementButton: managementSpotlightTarget,
                        pluginToggle: pluginToggle,
                        agentMasterToggle: agentMasterToggle,
                        catPawButton: catPawButton
                    }, runId, scaleSceneMs);
                    pluginPreviewCleanedUp = true;
                    shouldCleanupPreviewState = false;
                }
                return pluginDashboardWindow;
            } finally {
                if (shouldCleanupPreviewState && !pluginPreviewCleanedUp) {
                    await this.cleanupPluginPreviewState({
                        catPawButton: catPawButton,
                        hoverTarget: hoveredPluginToggle
                    }).catch(() => {});
                }
            }
        },

        finishPluginDashboardHandoff(reason) {
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || typeof handoff.resolve !== 'function') {
                return false;
            }
            handoff.failureReason = typeof reason === 'string' && reason
                ? reason
                : 'plugin_dashboard_finished_by_home';
            handoff.resolve(false);
            return true;
        },

        async waitForPluginDashboardPerformanceUntilNarrationBoundary(windowRef, payload, options) {
            const normalizedOptions = options && typeof options === 'object' ? options : {};
            const narrationDurationMs = Number.isFinite(normalizedOptions.narrationDurationMs)
                ? Math.max(0, Math.round(normalizedOptions.narrationDurationMs))
                : 0;
            const elapsedNarrationMs = Number.isFinite(normalizedOptions.elapsedNarrationMs)
                ? Math.max(0, Math.round(normalizedOptions.elapsedNarrationMs))
                : 0;
            const remainingNarrationMs = Math.max(0, narrationDurationMs - elapsedNarrationMs);
            const performancePromise = this.waitForPluginDashboardPerformance(windowRef, payload).catch(() => false);
            if (narrationDurationMs <= 0) {
                return await performancePromise;
            }

            let settled = false;
            let boundaryTimer = 0;
            let graceTimer = 0;
            const boundaryPromise = new Promise((resolve) => {
                boundaryTimer = window.setTimeout(() => {
                    boundaryTimer = 0;
                    if (settled || this.angryExitTriggered || this.destroyed) {
                        resolve(false);
                        return;
                    }
                    this.notifyPluginDashboardNarrationFinished();
                    graceTimer = window.setTimeout(() => {
                        graceTimer = 0;
                        if (!settled) {
                            this.finishPluginDashboardHandoff('plugin_dashboard_done_grace_timeout');
                        }
                        resolve(false);
                    }, DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS);
                }, remainingNarrationMs);
            });

            try {
                return await Promise.race([performancePromise, boundaryPromise]);
            } finally {
                settled = true;
                if (boundaryTimer) {
                    window.clearTimeout(boundaryTimer);
                    boundaryTimer = 0;
                }
                if (graceTimer) {
                    window.clearTimeout(graceTimer);
                    graceTimer = 0;
                }
            }
        },

        async waitForPluginDashboardPerformance(windowRef, payload) {
            if (!windowRef || windowRef.closed) {
                this.recordExperienceMetric('handoff_failed', {
                    sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                    targetPage: 'plugin_dashboard',
                    reason: 'plugin_dashboard_window_missing'
                });
                return Promise.resolve(false);
            }

            if (this.pluginDashboardHandoff && typeof this.pluginDashboardHandoff.reject === 'function') {
                this.pluginDashboardHandoff.reject(new Error('plugin-dashboard handoff superseded'));
            }

            const skipButtonScreenRect = await this.getSkipButtonScreenRect();

            return new Promise((resolve, reject) => {
                this.pluginDashboardLastInterruptRequestId = '';
                const sessionId = 'plugin-dashboard-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
                const startedAt = Date.now();
                const handoffPayload = Object.assign({}, payload || {}, {
                    interruptCount: Math.max(0, Math.floor(Number.isFinite(this.interruptCount) ? this.interruptCount : 0)),
                    skipButtonScreenRect: skipButtonScreenRect,
                    platformCapabilities: {
                        version: 1,
                        platform: this.platformCapabilities && this.platformCapabilities.platform
                            ? this.platformCapabilities.platform
                            : 'web',
                        windowBoundsSource: this.platformCapabilities && this.platformCapabilities.windowBoundsSource
                            ? this.platformCapabilities.windowBoundsSource
                            : 'browser-screen-origin',
                        supportsExternalChat: !!(this.platformCapabilities && this.platformCapabilities.supportsExternalChat),
                        supportsSystemTrayHint: !!(this.platformCapabilities && this.platformCapabilities.supportsSystemTrayHint),
                        supportsPluginDashboardWindow: !!(this.platformCapabilities && this.platformCapabilities.supportsPluginDashboardWindow),
                        pointerProfile: this.platformCapabilities && this.platformCapabilities.pointerProfile
                            ? this.platformCapabilities.pointerProfile
                            : 'pointer',
                        preferredSkipHitPadding: this.platformCapabilities && Number.isFinite(this.platformCapabilities.preferredSkipHitPadding)
                            ? this.platformCapabilities.preferredSkipHitPadding
                            : 18
                    }
                });
                const preloadTimeoutMs = 15000;
                const handoffVoiceDurationMs = this.getGuideVoiceDurationMs(
                    handoffPayload && handoffPayload.voiceKey,
                    resolveGuideLocale()
                );
                const executionTimeoutMs = clamp(
                    (handoffVoiceDurationMs > 0 ? handoffVoiceDurationMs : 0) + 12000,
                    12000,
                    42000
                );
                const targetOrigin = this.getPluginDashboardExpectedOrigin();
                if (!targetOrigin) {
                    this.recordExperienceMetric('handoff_failed', {
                        sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                        targetPage: 'plugin_dashboard',
                        reason: 'target_origin_missing'
                    });
                    resolve(false);
                    return;
                }
                const handoff = {
                    sessionId: sessionId,
                    windowRef: windowRef,
                    targetOrigin: targetOrigin,
                    ready: false,
                    readyAt: 0,
                    failureReason: '',
                    resolve: (result) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        if (!result) {
                            this.recordExperienceMetric('handoff_failed', {
                                sceneId: this.currentSceneId || 'plugin_dashboard_handoff',
                                targetPage: 'plugin_dashboard',
                                reason: handoff.failureReason || 'unknown'
                            });
                        }
                        resolve(result);
                    },
                    reject: (error) => {
                        if (this.pluginDashboardHandoff !== handoff) {
                            return;
                        }
                        if (handoff.intervalId) {
                            window.clearInterval(handoff.intervalId);
                            handoff.intervalId = 0;
                        }
                        if (handoff.timeoutId) {
                            window.clearTimeout(handoff.timeoutId);
                            handoff.timeoutId = 0;
                        }
                        this.pluginDashboardHandoff = null;
                        reject(error);
                    },
                    post: () => {
                        if (!windowRef || windowRef.closed) {
                            handoff.failureReason = 'plugin_dashboard_window_closed';
                            handoff.resolve(false);
                            return;
                        }
                        try {
                            windowRef.postMessage({
                                type: PLUGIN_DASHBOARD_HANDOFF_EVENT,
                                sessionId: sessionId,
                                payload: handoffPayload
                            }, handoff.ready ? handoff.targetOrigin : '*');
                        } catch (error) {
                            console.warn('[YuiGuide] 向插件面板发送 handoff 消息失败:', error);
                        }
                    }
                };

                handoff.intervalId = window.setInterval(() => {
                    if (!windowRef || windowRef.closed) {
                        handoff.failureReason = 'plugin_dashboard_window_closed';
                        handoff.resolve(false);
                        return;
                    }

                    if (!handoff.ready && (Date.now() - startedAt) >= preloadTimeoutMs) {
                        handoff.failureReason = 'plugin_dashboard_ready_timeout';
                        handoff.resolve(false);
                        return;
                    }

                    if (handoff.ready && handoff.readyAt > 0 && (Date.now() - handoff.readyAt) >= executionTimeoutMs) {
                        handoff.failureReason = 'plugin_dashboard_execution_timeout';
                        handoff.resolve(false);
                        return;
                    }
                    if (!handoff.ready) {
                        handoff.post();
                    }
                }, 450);
                handoff.timeoutId = window.setTimeout(() => {
                    handoff.failureReason = handoff.ready ? 'plugin_dashboard_execution_timeout' : 'plugin_dashboard_ready_timeout';
                    handoff.resolve(false);
                }, preloadTimeoutMs + executionTimeoutMs);

                this.pluginDashboardHandoff = handoff;
                handoff.post();
            });
        },

        dispatchDesktopPluginDashboardInterruptAck(payload) {
            try {
                window.dispatchEvent(new CustomEvent(DESKTOP_PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT, {
                    detail: payload && typeof payload === 'object' ? payload : {}
                }));
            } catch (error) {
                console.warn('[YuiGuide] 发送桌面插件面板 interrupt ack 失败:', error);
            }
        },

        dispatchDesktopPluginDashboardNarrationFinished(payload) {
            try {
                window.dispatchEvent(new CustomEvent(DESKTOP_PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT, {
                    detail: payload && typeof payload === 'object' ? payload : {}
                }));
            } catch (error) {
                console.warn('[YuiGuide] 发送桌面插件面板 narration finished 失败:', error);
            }
        },

        dispatchDesktopPluginDashboardSystemCursorTemporaryReveal(payload) {
            try {
                window.dispatchEvent(new CustomEvent(DESKTOP_PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT, {
                    detail: payload && typeof payload === 'object' ? payload : {}
                }));
            } catch (error) {
                console.warn('[YuiGuide] 发送桌面插件面板真实鼠标临时显示失败:', error);
            }
        },

        notifyPluginDashboardNarrationFinished() {
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return;
            }

            const payload = {
                type: PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
                sessionId: handoff.sessionId
            };
            this.dispatchDesktopPluginDashboardNarrationFinished(payload);

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!windowRef || windowRef.closed) {
                return;
            }

            try {
                windowRef.postMessage(payload, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
            } catch (error) {
                console.warn('[YuiGuide] 向插件面板发送 narration finished 失败:', error);
            }
        },

        notifyPluginDashboardSystemCursorTemporaryReveal(durationMs, reason) {
            const handoff = this.pluginDashboardHandoff;
            if (!handoff || !handoff.sessionId) {
                return false;
            }

            const payload = {
                type: PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT,
                sessionId: handoff.sessionId,
                durationMs: Math.min(10000, Math.max(0, Math.floor(Number(durationMs) || 0))),
                reason: typeof reason === 'string' && reason.trim() ? reason.trim() : 'tutorial-temporary-reveal'
            };
            this.dispatchDesktopPluginDashboardSystemCursorTemporaryReveal(payload);

            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!windowRef || windowRef.closed) {
                return true;
            }

            try {
                windowRef.postMessage(payload, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 向插件面板发送真实鼠标临时显示失败:', error);
                return false;
            }
        },

        notifyPluginDashboardTerminationRequested(reason) {
            const handoff = this.pluginDashboardHandoff;
            const windowRef = handoff && handoff.windowRef ? handoff.windowRef : null;
            if (!handoff || !windowRef || windowRef.closed || !handoff.sessionId) {
                return false;
            }

            try {
                windowRef.postMessage({
                    type: PLUGIN_DASHBOARD_TERMINATE_EVENT,
                    sessionId: handoff.sessionId,
                    reason: typeof reason === 'string' && reason.trim() ? reason.trim() : 'skip',
                    closeWindow: true
                }, handoff.targetOrigin || this.getPluginDashboardExpectedOrigin());
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 向插件面板发送 terminate 失败:', error);
                return false;
            }
        },

        async getGuideHostWindowBounds() {
            const bridge = window.nekoPetDrag;
            if (!bridge || typeof bridge.getBounds !== 'function') {
                return null;
            }

            try {
                const bounds = await Promise.race([
                    Promise.resolve(bridge.getBounds()),
                    new Promise((resolve) => window.setTimeout(() => resolve(null), 180))
                ]);
                if (!bounds || typeof bounds !== 'object') {
                    return null;
                }

                const x = Number(bounds.x);
                const y = Number(bounds.y);
                const width = Number(bounds.width);
                const height = Number(bounds.height);
                if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
                    return null;
                }

                return {
                    x: Math.round(x),
                    y: Math.round(y),
                    width: Math.round(width),
                    height: Math.round(height),
                    source: 'electron-window-bounds'
                };
            } catch (_) {
                return null;
            }
        },

        async getSkipButtonScreenRect() {
            const skipButton = document.getElementById('neko-tutorial-skip-btn');
            if (!skipButton || typeof skipButton.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = skipButton.getBoundingClientRect();
            if (!(rect.width > 0) || !(rect.height > 0)) {
                return null;
            }

            const hostBounds = await this.getGuideHostWindowBounds();
            const rawScreenLeft = hostBounds && Number.isFinite(hostBounds.x)
                ? hostBounds.x
                : Number.isFinite(Number(window.screenX))
                ? Number(window.screenX)
                : Number(window.screenLeft);
            const rawScreenTop = hostBounds && Number.isFinite(hostBounds.y)
                ? hostBounds.y
                : Number.isFinite(Number(window.screenY))
                ? Number(window.screenY)
                : Number(window.screenTop);
            const screenLeft = Number.isFinite(rawScreenLeft) ? rawScreenLeft : 0;
            const screenTop = Number.isFinite(rawScreenTop) ? rawScreenTop : 0;
            const boundsSource = hostBounds && hostBounds.source
                ? hostBounds.source
                : (this.platformCapabilities && this.platformCapabilities.windowBoundsSource) || 'browser-screen-origin';
            const hitPadding = this.platformCapabilities && typeof this.platformCapabilities.getSkipHitPadding === 'function'
                ? this.platformCapabilities.getSkipHitPadding(boundsSource)
                : 18;

            return {
                left: Math.round(screenLeft + rect.left - hitPadding),
                top: Math.round(screenTop + rect.top - hitPadding),
                right: Math.round(screenLeft + rect.right + hitPadding),
                bottom: Math.round(screenTop + rect.bottom + hitPadding),
                coordinateSpace: boundsSource,
                platform: this.platformCapabilities && this.platformCapabilities.platform
                    ? this.platformCapabilities.platform
                    : 'web',
                devicePixelRatio: Number.isFinite(Number(window.devicePixelRatio)) ? Number(window.devicePixelRatio) : 1,
                hitPadding: hitPadding,
                forwardingTolerance: this.platformCapabilities && typeof this.platformCapabilities.getSkipForwardingTolerance === 'function'
                    ? this.platformCapabilities.getSkipForwardingTolerance({
                        coordinateSpace: boundsSource,
                        hitPadding: hitPadding
                    })
                    : 6,
                pointerProfile: this.platformCapabilities && this.platformCapabilities.pointerProfile
                    ? this.platformCapabilities.pointerProfile
                    : 'pointer'
            };
        },

        beginTerminationVisualCleanup() {
            this.sceneRunId += 1;
            this.restoreDay1TakeoverAgentSwitches('termination_cleanup').catch((error) => {
                console.warn('[YuiGuide] 终止时恢复 Day1 Agent 开关失败:', error);
            });
            this.stopPluginDashboardCornerPeekPerformance(this.takeoverTopPeekHandle, 'termination_cleanup').catch(() => {});
            this.takeoverTopPeekHandle = null;
            this.stopGuideIdleSwayPerformance('termination_cleanup').catch(() => {});
            if (this.preTakeoverGhostCursorLookAtHandle) {
                this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    'termination_cleanup'
                ).catch(() => {});
            }
            this.stopPersistentGhostCursorLookAtPerformance('termination_cleanup').catch(() => {});
            this.resumeCurrentSceneAfterResistance();
            this.setCurrentScene(null, null);
            this.clearSceneTimers();
            this.disableInterrupts();
            this.cancelActiveNarration();
            this.clearUserCursorRevealSuppression(true);
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            this.awaitingIntroActivation = false;
            if (typeof this._introActivationResolve === 'function') {
                this._introActivationResolve();
                this._introActivationResolve = null;
            }
            if (this.wakeup && typeof this.wakeup.cancel === 'function') {
                this.wakeup.cancel('termination');
            }
            if (this.resistanceController && typeof this.resistanceController.destroy === 'function') {
                this.resistanceController.destroy();
            }
            if (this.interactionTakeover && typeof this.interactionTakeover.clearExternalizedChatFx === 'function') {
                this.interactionTakeover.clearExternalizedChatFx();
            }
            if (this.latestGuideChatMessageRetainTimer) {
                window.clearTimeout(this.latestGuideChatMessageRetainTimer);
                this.latestGuideChatMessageRetainTimer = null;
            }
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.clearGuideChatStreamTimers();
            this.clearGuideChatMessages();
            this.clearQueuedGuideChatBridgeMessages();
            this.setDay4LockSpotlightSafeAreaActive(false, 'termination-cleanup');
            if (this.overlay && typeof this.overlay.setSpotlightSuppressed === 'function') {
                this.overlay.setSpotlightSuppressed(true);
            }
            this.clearIntroFlow();
            this.voiceQueue.stop();
            this.clearAllVirtualSpotlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
            if (this.spotlightController && typeof this.spotlightController.destroy === 'function') {
                this.spotlightController.destroy();
            }
            this.cleanupTutorialReturnButtons();
            this.customSecondarySpotlightTarget = null;
            if (this.page === 'home') {
                document.body.classList.remove('yui-guide-home-ui-suppressed');
            }
            this.cursor.cancel();
            this.cursor.hide();
            this.performFullCleanup({
                destroyInteractionTakeover: true,
                destroyOverlay: true
            });
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            this.hideTemporaryAvatarFloatingGuideHud('termination-cleanup');
            this.closeManagedPanels().catch((error) => {
                console.warn('[YuiGuide] 终止时关闭首页面板失败:', error);
            });
            this.closePluginDashboardWindowIfCreatedByGuide('终止');
            if (typeof window.handleShowMainUI === 'function') {
                try {
                    window.handleShowMainUI();
                } catch (error) {
                    console.warn('[YuiGuide] 终止时恢复主界面失败:', error);
                }
            }
        },
    });
})(window.__YuiGuideDirector);
