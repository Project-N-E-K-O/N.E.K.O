(function (namespace) {
    'use strict';

    const {
        resolveGuideLocale,
        recordAvatarFloatingGuideRoundStart,
        recordAvatarFloatingGuideRoundEnd,
        DEFAULT_CURSOR_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_AGENT_PANEL_CURSOR_MOVE_MS,
        DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS,
        DAY6_PLUGIN_AGENT_PANEL_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_CAT_PAW_CURSOR_OFFSET_Y,
        DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS,
        DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS,
        DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_SIDE_PANEL_ACTION_TIMEOUT_MS,
        DAY6_PLUGIN_SIDE_PANEL_DASHBOARD_WAIT_MS,
        INTRO_ACTIVATION_HINT_KEY,
        INTRO_ACTIVATION_HINT,
        DEFAULT_SPOTLIGHT_PADDING,
        PLUGIN_DASHBOARD_WINDOW_NAME,
        getYuiGuideDailyGuide,
        TAKEOVER_CAPTURE_SELECTORS,
        AVATAR_FLOATING_GUIDE_INTERRUPT_STEP,
        wait,
        clamp
    } = namespace;

    namespace.extendDirector({
        getAvatarFloatingRoundConfig(round) {
            const guideConfig = getYuiGuideDailyGuide(Number(round));
            return guideConfig && guideConfig.round ? guideConfig.round : null;
        },

        getAvatarFloatingInterruptStep(scene) {
            const normalizedScene = scene || {};
            return {
                id: normalizedScene.id || AVATAR_FLOATING_GUIDE_INTERRUPT_STEP.id,
                anchor: normalizedScene.target || '',
                performance: {
                    interruptible: normalizedScene.interruptible !== false,
                    bubbleText: normalizedScene.text || '',
                    bubbleTextKey: normalizedScene.textKey || '',
                    voiceKey: normalizedScene.voiceKey || '',
                    emotion: normalizedScene.emotion || '',
                    cursorTarget: normalizedScene.cursorTarget || normalizedScene.target || '',
                    cursorAction: normalizedScene.cursorAction || ''
                },
                interrupts: AVATAR_FLOATING_GUIDE_INTERRUPT_STEP.interrupts
            };
        },

        getAvatarFloatingBaseTarget(kind) {
            if (kind === 'chat-window') {
                return this.getChatWindowTarget() || this.getChatInputTarget();
            }
            if (kind === 'floating-buttons') {
                return this.resolveElement('#${p}-floating-buttons');
            }
            return null;
        },

        setAvatarFloatingToolbarVisible(visible, reason) {
            const shouldShow = visible !== false;
            window.nekoYuiGuideFloatingToolbarSuppressed = !shouldShow;
            if (document && document.body && document.body.classList) {
                document.body.classList.toggle('yui-guide-floating-toolbar-suppressed', !shouldShow);
            }
            window.dispatchEvent(new CustomEvent('neko:yui-guide-floating-toolbar-suppression-change', {
                detail: {
                    suppressed: !shouldShow,
                    reason: reason || ''
                }
            }));
            if (shouldShow) {
                return;
            }

            this.forceHideAvatarFloatingGuideManagedSurfaces();
        },

        revealAvatarFloatingToolbarForGuideInteraction(reason) {
            this.setAvatarFloatingToolbarVisible(true, reason || 'guide-interaction');
            const toolbar = this.getAvatarFloatingBaseTarget('floating-buttons');
            if (!toolbar || !toolbar.style) {
                return false;
            }
            if (toolbar.dataset && toolbar.dataset.yuiGuideForcedHidden === 'true') {
                delete toolbar.dataset.yuiGuideForcedHidden;
            }
            toolbar.style.removeProperty('display');
            toolbar.style.removeProperty('visibility');
            toolbar.style.removeProperty('opacity');
            toolbar.style.removeProperty('pointer-events');
            toolbar.style.setProperty('display', 'flex', 'important');
            toolbar.style.setProperty('visibility', 'visible', 'important');
            toolbar.style.setProperty('opacity', '1', 'important');
            toolbar.style.setProperty('pointer-events', 'auto', 'important');
            return true;
        },

        shouldShowAvatarFloatingToolbarForScene(scene) {
            const normalizedScene = scene || {};
            const sceneId = typeof normalizedScene.id === 'string'
                ? normalizedScene.id
                : '';
            const day4SettingsSceneIds = [
                'day4_chat_settings',
                'day4_model_behavior',
                'day4_gaze_follow',
                'day4_privacy_mode'
            ];
            const day3SettingsSceneIds = [
                'day3_personalization_space',
                'day3_personalization_detail',
                'day3_proactive_chat'
            ];
            const day5SettingsSceneIds = [
                'day5_character_settings',
                'day5_character_panic',
                'day5_memory_entry'
            ];
            if (
                day3SettingsSceneIds.includes(sceneId)
                || day4SettingsSceneIds.includes(sceneId)
                || day5SettingsSceneIds.includes(sceneId)
            ) {
                return true;
            }

            const topLevelTargets = [
                '#${p}-floating-buttons',
                '#${p}-btn-mic',
                '#${p}-btn-screen',
                '#${p}-btn-agent',
                '#${p}-btn-settings',
                '#${p}-btn-goodbye',
                '#${p}-btn-return',
                '#${p}-lock-icon',
                'floating-buttons'
            ];
            const settingsPanelTargets = [
                '#${p}-menu-character',
                '#${p}-menu-memory',
                '#${p}-toggle-proactive-chat'
            ];
            const targetFields = [
                normalizedScene.target,
                normalizedScene.secondary,
                normalizedScene.cursorTarget,
                normalizedScene.persistent
            ].filter((value) => typeof value === 'string');
            if (targetFields.some((target) => topLevelTargets.includes(target))) {
                return true;
            }
            if (targetFields.some((target) => settingsPanelTargets.includes(target))) {
                return true;
            }

            const operation = typeof normalizedScene.operation === 'string'
                ? normalizedScene.operation
                : '';
            return !!(
                operation === 'day1-intro-basic-voice-showcase'
                || operation === 'day3-open-settings-personalization'
                || operation === 'day3-settings-detail'
                || operation.indexOf('day1-managed-scene:') === 0
                || operation.indexOf('show-settings-menu:') === 0
                || operation.indexOf('show-settings-sidepanel:') === 0
                || operation.indexOf('show-agent-sidepanel:') === 0
                || operation === 'day6-plugin-open-agent-panel-flow'
                || operation === 'day6-plugin-open-management-panel-flow'
                || operation === 'day6-plugin-sidepanel-flow'
            );
        },

        syncAvatarFloatingToolbarForScene(scene, reason) {
            this.setAvatarFloatingToolbarVisible(
                this.shouldShowAvatarFloatingToolbarForScene(scene),
                reason || (scene && scene.id) || 'scene'
            );
        },

        isAvatarFloatingInputIntroScene(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return !!(
                sceneId === 'day1_intro_activation'
                || sceneId === 'day2_tool_toggle_intro'
                || sceneId === 'day3_intro_context'
                || sceneId === 'day4_intro_companion'
                || sceneId === 'day5_character_settings'
                || sceneId === 'day6_intro_agent'
                || sceneId === 'day7_memory_review'
            );
        },

        getAvatarFloatingIntroSpotlightTarget(scene) {
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return this.getChatCapsuleInputTarget() || this.getChatInputTarget() || this.getChatWindowTarget();
            }
            return this.getAvatarFloatingBaseTarget('chat-window');
        },

        getAvatarFloatingIntroExternalizedSpotlightKind(scene) {
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return 'capsule-input';
            }
            return 'window';
        },

        getAvatarFloatingIntroExternalizedCursorOptions(scene) {
            if (scene && scene.id === 'day1_intro_activation') {
                return {
                    effect: this.getExternalizedChatCursorEffect(scene),
                    durationMs: 0
                };
            }
            if (this.isAvatarFloatingInputIntroScene(scene)) {
                return {
                    effect: '',
                    durationMs: 0
                };
            }
            return {
                effect: this.getExternalizedChatCursorEffect(scene)
            };
        },

        getAvatarFloatingSidePanel(type) {
            const normalizedType = typeof type === 'string' ? type.trim() : '';
            return normalizedType
                ? document.querySelector('[data-neko-sidepanel-type="' + normalizedType + '"]')
                : null;
        },

        collapseAvatarFloatingSidePanelsExcept(currentPanel) {
            document.querySelectorAll('[data-neko-sidepanel]').forEach((panel) => {
                if (!panel || panel === currentPanel) {
                    return;
                }

                if (panel._hoverCollapseTimer) {
                    window.clearTimeout(panel._hoverCollapseTimer);
                    panel._hoverCollapseTimer = null;
                }
                if (panel._expandFrameId) {
                    window.cancelAnimationFrame(panel._expandFrameId);
                    panel._expandFrameId = null;
                }
                if (typeof panel._stopHoverPointerTracking === 'function') {
                    panel._stopHoverPointerTracking();
                }
                if (typeof panel._collapse === 'function') {
                    panel._collapse();
                    return;
                }

                if (panel._collapseTimeout) {
                    window.clearTimeout(panel._collapseTimeout);
                    panel._collapseTimeout = null;
                }
                panel.style.transition = 'none';
                panel.style.opacity = '0';
                panel.style.display = 'none';
                panel.style.pointerEvents = 'none';
                panel.style.transition = '';
            });
        },

        forceHideAvatarFloatingSidePanel(panel) {
            if (!panel) {
                return false;
            }

            if (panel._hoverCollapseTimer) {
                window.clearTimeout(panel._hoverCollapseTimer);
                panel._hoverCollapseTimer = null;
            }
            if (panel._collapseTimeout) {
                window.clearTimeout(panel._collapseTimeout);
                panel._collapseTimeout = null;
            }
            if (panel._expandFrameId) {
                window.cancelAnimationFrame(panel._expandFrameId);
                panel._expandFrameId = null;
            }
            if (typeof panel._stopHoverPointerTracking === 'function') {
                panel._stopHoverPointerTracking();
            }

            panel._visibilityRevision = (panel._visibilityRevision || 0) + 1;
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            panel.style.pointerEvents = 'none';
            panel.style.left = '';
            panel.style.right = '';
            panel.style.top = '';
            panel.style.transform = '';
            panel.style.transition = '';
            return true;
        },

        forceHideAvatarFloatingSidePanels() {
            const popupUi = window.AvatarPopupUI || null;
            if (popupUi && typeof popupUi.collapseOtherSidePanels === 'function') {
                try {
                    popupUi.collapseOtherSidePanels(null);
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 强制隐藏首页侧面板失败，回退到本地隐藏:', error);
                }
            }

            let hidden = false;
            document.querySelectorAll('[data-neko-sidepanel]').forEach((panel) => {
                hidden = this.forceHideAvatarFloatingSidePanel(panel) || hidden;
            });
            return hidden;
        },

        positionAvatarFloatingSidePanelNow(panel) {
            const targetPanel = panel || null;
            const anchor = targetPanel && targetPanel._anchorElement ? targetPanel._anchorElement : null;
            const popupUi = window.AvatarPopupUI || null;
            if (!targetPanel || !anchor || !popupUi || typeof popupUi.positionSidePanel !== 'function') {
                return false;
            }

            try {
                popupUi.positionSidePanel(targetPanel, anchor);
                return true;
            } catch (error) {
                console.warn('[YuiGuide] positionAvatarFloatingSidePanelNow 失败:', error);
                return false;
            }
        },

        refreshAvatarFloatingSettingsPanelLayout(panel) {
            const popupPositioned = this.positionManagedPanelNow('settings');
            const sidePanelPositioned = panel && this.isElementVisible(panel)
                ? this.positionAvatarFloatingSidePanelNow(panel)
                : false;
            return popupPositioned || sidePanelPositioned;
        },

        forceHideAvatarFloatingGuideManagedSurfaces() {
            this.forceHideManagedPanel('settings');
            this.forceHideManagedPanel('agent');
            this.forceHideAvatarFloatingSidePanels();
        },

        hideTemporaryAvatarFloatingGuideHud(reason) {
            if (
                this.avatarFloatingGuideTemporaryHudShown
                && !this.avatarFloatingGuideTemporaryHudWasVisible
                && window.AgentHUD
                && typeof window.AgentHUD.hideAgentTaskHUD === 'function'
            ) {
                try {
                    window.AgentHUD.hideAgentTaskHUD();
                } catch (error) {
                    console.warn('[YuiGuide] 隐藏教程临时任务 HUD 失败:', reason || 'cleanup', error);
                }
            }
            this.avatarFloatingGuideTemporaryHudShown = false;
            this.avatarFloatingGuideTemporaryHudWasVisible = false;
        },

        async expandAvatarFloatingSidePanel(panel, anchor) {
            if (!panel) {
                return false;
            }
            const targetAnchor = anchor || panel._anchorElement || null;
            if (targetAnchor) {
                this.refreshAvatarFloatingSettingsPanelLayout(panel);
            }
            this.collapseAvatarFloatingSidePanelsExcept(panel);
            if (typeof panel._expand === 'function') {
                if (panel._hoverCollapseTimer) {
                    window.clearTimeout(panel._hoverCollapseTimer);
                    panel._hoverCollapseTimer = null;
                }
                panel._expand();
            } else if (targetAnchor) {
                try {
                    targetAnchor.dispatchEvent(new MouseEvent('mouseenter', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                } catch (_) {}
            }

            return !!(await this.waitForElement(() => {
                return this.isElementVisible(panel) && panel.style.display !== 'none' && panel.style.opacity !== '0'
                    ? panel
                    : null;
            }, 1400));
        },

        async ensureAvatarFloatingSettingsSidePanel(type, options) {
            const shouldContinue = options && typeof options.shouldContinue === 'function'
                ? options.shouldContinue
                : null;
            const skipOpenSettingsPanel = !!(options && options.skipOpenSettingsPanel);
            if (shouldContinue && !shouldContinue()) {
                return null;
            }
            if (!skipOpenSettingsPanel) {
                const opened = await this.openSettingsPanel();
                if (!opened) {
                    return null;
                }
                this.positionManagedPanelNow('settings');
            }
            if (this.isStopping()) {
                return null;
            }
            const panel = await this.waitForElement(() => this.getAvatarFloatingSidePanel(type), 1200);
            if (!panel) {
                return null;
            }
            this.sidebarPauseController.trackPanel(panel);
            this.refreshAvatarFloatingSettingsPanelLayout(panel);
            if (shouldContinue && !shouldContinue()) {
                return null;
            }
            const expanded = await this.expandAvatarFloatingSidePanel(panel, panel._anchorElement || null);
            if (!expanded || (shouldContinue && !shouldContinue())) {
                return null;
            }
            this.refreshAvatarFloatingSettingsPanelLayout(panel);
            return panel;
        },

        async ensureAvatarFloatingAgentSidePanel(toggleId) {
            const normalizedToggleId = toggleId === 'openclaw' ? 'agent-openclaw' : 'agent-user-plugin';
            const ready = await this.ensureAgentSidePanelVisible(normalizedToggleId);
            if (!ready || this.isStopping()) {
                return null;
            }
            return this.getAvatarFloatingSidePanel(normalizedToggleId + '-actions');
        },

        getAvatarFloatingAgentCapabilityTargets() {
            return [
                'agent-keyboard',
                'agent-browser',
                'agent-openfang',
                'agent-user-plugin',
                'agent-openclaw'
            ].map((toggleId) => this.getAgentToggleElement(toggleId)).filter(Boolean);
        },

        getAvatarFloatingVisibleChildren(panel, limit) {
            if (!panel || typeof panel.querySelectorAll !== 'function') {
                return [];
            }
            const maxItems = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : 4;
            return Array.from(panel.querySelectorAll('button, [role="button"], [role="switch"], input, a, [id]'))
                .filter((element) => element !== panel && this.isElementVisible(element))
                .slice(0, maxItems);
        },

        getAvatarFloatingCursorTourTargets(scene, primaryTarget) {
            const normalizedScene = scene || {};
            const targetKey = typeof normalizedScene.target === 'string' ? normalizedScene.target : '';
            const operation = typeof normalizedScene.operation === 'string' ? normalizedScene.operation : '';
            if (targetKey === 'agent-capabilities') {
                return this.getAvatarFloatingAgentCapabilityTargets();
            }
            if (targetKey.indexOf('settings-sidepanel:') === 0) {
                return this.getAvatarFloatingVisibleChildren(
                    this.getAvatarFloatingSidePanel(targetKey.split(':')[1] || ''),
                    4
                );
            }
            if (operation.indexOf('show-settings-sidepanel:') === 0) {
                return this.getAvatarFloatingVisibleChildren(
                    this.getAvatarFloatingSidePanel(operation.split(':')[1] || ''),
                    4
                );
            }
            if (primaryTarget && primaryTarget.hasAttribute && primaryTarget.hasAttribute('data-neko-sidepanel')) {
                return this.getAvatarFloatingVisibleChildren(primaryTarget, 4);
            }
            if (
                primaryTarget
                && primaryTarget.id === 'agent-task-hud'
            ) {
                return this.getAvatarFloatingVisibleChildren(primaryTarget, 4);
            }
            return [];
        },

        getChatAvatarToolMenuTargets(limit) {
            const popover = this.getVisibleChatAvatarToolMenuPopover();
            if (!popover || typeof popover.querySelectorAll !== 'function' || !this.isElementVisible(popover)) {
                return [];
            }
            const maxItems = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : 3;
            const targets = Array.from(popover.querySelectorAll('.composer-icon-button[data-avatar-tool-id]'));
            const fallbackTargets = targets.length
                ? targets
                : Array.from(popover.querySelectorAll('.composer-icon-button'));
            return fallbackTargets
                .filter((element) => this.isElementVisible(element))
                .slice(0, maxItems);
        },

        getVisibleChatAvatarToolMenuPopover() {
            const selectors = [
                '#composer-tool-popover',
                '#composer-tool-popover-compact',
                '#react-chat-window-root .composer-icon-popover',
                '.composer-icon-popover'
            ];
            for (let index = 0; index < selectors.length; index += 1) {
                const candidate = this.resolveElement(selectors[index]);
                if (candidate && this.isElementVisible(candidate)) {
                    return candidate;
                }
            }
            return null;
        },

        async waitForAvatarToolMenuTargets(minTargets, timeoutMs) {
            const minimum = Number.isFinite(minTargets) ? Math.max(1, Math.floor(minTargets)) : 3;
            const targets = await this.waitForElement(() => {
                const items = this.getChatAvatarToolMenuTargets(minimum);
                return items.length >= minimum ? items : null;
            }, Number.isFinite(timeoutMs) ? timeoutMs : 900);
            return Array.isArray(targets) ? targets : [];
        },

        getVisibleChatComposerElement(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return null;
            }
            const scopedCandidates = Array.from(document.querySelectorAll('#react-chat-window-root ' + selector));
            const globalCandidates = Array.from(document.querySelectorAll(selector));
            return scopedCandidates.concat(globalCandidates)
                .filter((element, index, array) => element && array.indexOf(element) === index)
                .find((element) => this.isElementVisible(element)) || null;
        },

        async ensureChatComposerOverflowMenuOpen() {
            const existingPopover = this.getVisibleChatComposerElement('.composer-overflow-popover');
            if (existingPopover) {
                return true;
            }
            const overflowButton = this.getVisibleChatComposerElement('.composer-overflow-btn');
            if (!overflowButton || typeof overflowButton.click !== 'function') {
                return false;
            }
            overflowButton.click();
            return !!(await this.waitForElement(() => this.getVisibleChatComposerElement('.composer-overflow-popover'), 900));
        },

        async getChatComposerToolButton(selector) {
            const direct = this.getVisibleChatComposerElement(selector);
            if (direct) {
                return direct;
            }
            if (await this.ensureChatComposerOverflowMenuOpen()) {
                return this.getVisibleChatComposerElement(selector);
            }
            return null;
        },

        applyCircleImageSpotlightHints(targets, padding) {
            const items = Array.isArray(targets) ? targets.filter(Boolean) : [];
            items.forEach((target) => {
                this.setSpotlightGeometryHint(target, {
                    padding: Number.isFinite(padding) ? padding : 6,
                    geometry: 'circle'
                });
            });
            this.setSpotlightVariantHints(items.map((element) => ({
                element,
                variant: 'circle-image'
            })));
            return items;
        },

        applyPlainCircularSpotlightHints(targets, padding) {
            const items = Array.isArray(targets) ? targets.filter(Boolean) : [];
            items.forEach((target) => {
                this.setSpotlightGeometryHint(target, {
                    padding: Number.isFinite(padding) ? padding : 6,
                    geometry: 'circle'
                });
            });
            this.setSpotlightVariantHints(items.map((element) => ({
                element,
                variant: 'plain-circle'
            })));
            return items;
        },

        applyChatAvatarToolButtonSpotlightHint(element) {
            if (!element) {
                return false;
            }
            this.setSpotlightGeometryHint(element, {
                padding: 4,
                geometry: 'circle'
            });
            this.setSpotlightVariantHints([{
                element,
                variant: 'circle-image'
            }]);
            return true;
        },

        keepAvatarToolButtonHighlightedAfterMenuOpen(element, scene) {
            if (!element) {
                return false;
            }
            this.applyChatAvatarToolButtonSpotlightHint(element);
            this.applyGuideHighlights({
                key: ((scene && scene.id) || 'avatar-tool-menu') + '-button-open',
                primary: element
            });
            return true;
        },

        setChatAvatarToolMenuOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setAvatarToolMenuOpen === 'function'
                && this.chatWindowAdapter.setAvatarToolMenuOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatAvatarToolMenuOpen === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatAvatarToolMenuOpen(desiredOpen, actionReason);
                return true;
            }
            const reactHost = window.reactChatWindowHost || null;
            if (
                !this.isHomeChatExternalized()
                && reactHost
                && typeof reactHost.setAvatarToolMenuOpen === 'function'
            ) {
                reactHost.setAvatarToolMenuOpen(desiredOpen, actionReason);
                return true;
            }
            return false;
        },

        clickChatAvatarToolButton(reason) {
            if (!this.isHomeChatExternalized()) {
                return false;
            }
            if (
                this.interactionTakeover
                && typeof this.interactionTakeover.clickExternalizedChatAvatarToolButton === 'function'
            ) {
                this.interactionTakeover.clickExternalizedChatAvatarToolButton(reason || 'avatar-floating-guide');
                return true;
            }
            return false;
        },

        setCompactToolFanOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactToolFanOpen === 'function'
                && this.chatWindowAdapter.setCompactToolFanOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (
                this.isHomeChatExternalized()
                && this.interactionTakeover
                && typeof this.interactionTakeover.setExternalizedChatCompactToolFanOpen === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCompactToolFanOpen(desiredOpen, actionReason);
                return true;
            }
            if (this.isHomeChatExternalized()) {
                return false;
            }
            const toggle = this.resolveAvatarFloatingSelector('chat-tool-toggle');
            const isOpen = !!(toggle && (
                toggle.getAttribute('aria-expanded') === 'true'
                || toggle.classList.contains('is-open')
            ));
            if (toggle && typeof toggle.click === 'function' && ((open === true && !isOpen) || (open !== true && isOpen))) {
                toggle.click();
                return true;
            }
            return false;
        },

        rotateCompactToolWheelForGuide(direction, stepCount, reason) {
            const normalizedDirection = Number(direction) < 0 ? -1 : 1;
            const normalizedStepCount = Number.isFinite(Number(stepCount))
                ? Math.max(1, Math.min(7, Math.floor(Number(stepCount))))
                : 1;
            return !!(
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.rotateCompactToolWheel === 'function'
                && this.chatWindowAdapter.rotateCompactToolWheel(
                    normalizedDirection,
                    normalizedStepCount,
                    reason || 'avatar-floating-guide'
                )
            );
        },

        setCompactToolWheelIndexForGuide(index, reason) {
            const normalizedIndex = Number.isFinite(Number(index))
                ? Math.max(0, Math.min(6, Math.floor(Number(index))))
                : 0;
            return !!(
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactToolWheelIndex === 'function'
                && this.chatWindowAdapter.setCompactToolWheelIndex(
                    normalizedIndex,
                    reason || 'avatar-floating-guide'
                )
            );
        },

        setCompactHistoryOpen(open, reason) {
            const desiredOpen = open === true;
            const actionReason = reason || 'avatar-floating-guide';
            if (
                this.chatWindowAdapter
                && typeof this.chatWindowAdapter.setCompactHistoryOpen === 'function'
                && this.chatWindowAdapter.setCompactHistoryOpen(desiredOpen, actionReason)
            ) {
                return true;
            }
            if (this.isHomeChatExternalized()) {
                if (
                    this.interactionTakeover
                    && typeof this.interactionTakeover.setExternalizedChatCompactHistoryOpen === 'function'
                ) {
                    this.interactionTakeover.setExternalizedChatCompactHistoryOpen(desiredOpen, actionReason);
                    return true;
                }
                return false;
            }
            const handle = this.resolveAvatarFloatingSelector('chat-history-handle');
            const isOpen = !!(handle && (
                handle.getAttribute('aria-expanded') === 'true'
                || handle.getAttribute('data-compact-history-open') === 'true'
            ));
            if (handle && typeof handle.click === 'function' && ((desiredOpen && !isOpen) || (!desiredOpen && isOpen))) {
                handle.click();
                return true;
            }
            return false;
        },

        getExternalizedChatTargetKind(targetKey, scene) {
            const registeredKind = this.spotlightController.getExternalKind(targetKey);
            if (registeredKind) {
                return registeredKind;
            }
            if (targetKey === 'chat-window') {
                return 'window';
            }
            if (targetKey === 'chat-tools') {
                return 'input';
            }
            return '';
        },

        getAvatarFloatingCursorTargetKey(scene) {
            if (!scene || typeof scene !== 'object') {
                return '';
            }
            return scene.cursorTarget || scene.target || '';
        },

        getExternalizedChatCursorTargetKind(scene) {
            const registeredKind = this.cursor.getExternalKind(this.getAvatarFloatingCursorTargetKey(scene));
            if (registeredKind) {
                return registeredKind;
            }
            return this.getExternalizedChatTargetKind(scene && scene.target || '', scene);
        },

        getExternalizedChatCursorEffect(scene) {
            if (scene && scene.id === 'day2_avatar_tools') {
                return 'move';
            }
            const action = scene && typeof scene.cursorAction === 'string'
                ? scene.cursorAction
                : '';
            if (action === 'click') {
                return 'click';
            }
            if (action === 'move') {
                return 'move';
            }
            if (scene && typeof scene.id === 'string') {
                const dayMatch = scene.id.match(/^day(\d+)_/);
                if (dayMatch && dayMatch[1] !== '1') {
                    return 'move';
                }
            }
            return 'wobble';
        },

        getExternalizedChatCursorMoveDurationMs(scene, fallbackMs) {
            if (this.isDay2InteractionSceneId(scene && scene.id)) {
                return 0;
            }
            if (scene && Number.isFinite(scene.cursorMoveDurationMs)) {
                return Math.max(160, Math.floor(scene.cursorMoveDurationMs));
            }
            const action = scene && typeof scene.cursorAction === 'string'
                ? scene.cursorAction
                : '';
            if (action === 'click') {
                return Number.isFinite(fallbackMs)
                    ? Math.max(160, Math.floor(fallbackMs))
                    : 760;
            }
            return 0;
        },

        setHomePcCursorOutputSuppressedForExternalizedChat(suppressed) {
            if (this.overlay && typeof this.overlay.setPcCursorOutputSuppressed === 'function') {
                this.overlay.setPcCursorOutputSuppressed(suppressed === true);
            }
        },

        clearHomeSpotlightsForExternalizedChat() {
            if (this.overlay && typeof this.overlay.clearSpotlight === 'function') {
                this.overlay.clearSpotlight({
                    preservePcOverlaySpotlights: true
                });
            }
        },

        hideHomeCursorForExternalizedChat() {
            if (
                this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
            ) {
                if (this.cursor && typeof this.cursor.clearPosition === 'function') {
                    this.cursor.cancel();
                    if (typeof this.cursor.hide === 'function') {
                        this.cursor.hide();
                    }
                    this.cursor.clearPosition();
                } else if (this.overlay && typeof this.overlay.clearCursorPosition === 'function') {
                    if (this.overlay && typeof this.overlay.hideCursor === 'function') {
                        this.overlay.hideCursor();
                    }
                    this.overlay.clearCursorPosition();
                }
                return;
            }
            this.cursor.hide();
        },

        setExternalizedChatGuideTarget(kind, options) {
            const normalizedKind = typeof kind === 'string' ? kind : '';
            if (
                !this.isHomeChatExternalized()
                || !normalizedKind
                || !this.interactionTakeover
            ) {
                return false;
            }
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.clearHomeSpotlightsForExternalizedChat();
                const spotlightVariant = options && typeof options.spotlightVariant === 'string'
                    ? options.spotlightVariant.trim()
                    : '';
                this.interactionTakeover.setExternalizedChatSpotlight(normalizedKind, {
                    variant: spotlightVariant
                });
            }
            this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            const effect = options && typeof options.effect === 'string' ? options.effect : 'wobble';
            const effectDurationMs = options && Number.isFinite(options.effectDurationMs)
                ? Math.max(0, Math.floor(options.effectDurationMs))
                : 0;
            this.rememberExternalizedChatCursorHandoffPoint(normalizedKind, effect);
            if (typeof this.interactionTakeover.setExternalizedChatCursor === 'function') {
                const cursorOptions = {
                    effect: effect,
                    effectDurationMs: effectDurationMs,
                    targetIndex: options && Number.isFinite(options.targetIndex)
                        ? Math.max(0, Math.floor(options.targetIndex))
                        : 0
                };
                if (options && Number.isFinite(options.durationMs)) {
                    cursorOptions.durationMs = Math.max(0, Math.floor(options.durationMs));
                }
                this.interactionTakeover.setExternalizedChatCursor(normalizedKind, cursorOptions);
            }
            this.hideHomeCursorForExternalizedChat();
            return true;
        },

        isDay2AvatarToolsSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day2_avatar_tools'
                    || sceneId.indexOf('day2_avatar_tools_') === 0
                )
            );
        },

        isDay2GalgameSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day2_galgame_games'
                    || sceneId.indexOf('day2_galgame_') === 0
                )
            );
        },

        isDay2WrapSceneId(sceneId) {
            return !!(
                typeof sceneId === 'string'
                && (
                    sceneId === 'day2_wrap'
                    || sceneId.indexOf('day2_wrap_') === 0
                )
            );
        },

        isDay2InteractionSceneId(sceneId) {
            return !!(
                sceneId === 'day2_tool_toggle_intro'
                || this.isDay2AvatarToolsSceneId(sceneId)
                || this.isDay2GalgameSceneId(sceneId)
                || this.isDay2WrapSceneId(sceneId)
            );
        },

        shouldPreserveExternalizedChatCursor(previousSceneId, scene) {
            const nextSceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return !!(
                (
                    previousSceneId === 'day2_intro_context'
                    && nextSceneId === 'day2_screen_entry'
                )
                || (
                    previousSceneId === 'day1_history_handle'
                    && nextSceneId === 'day1_intro_basic_voice'
                )
                || (
                    previousSceneId === 'day1_intro_basic_voice'
                    && nextSceneId === 'day1_screen_entry'
                )
                || (
                    previousSceneId === 'day1_screen_entry'
                    && nextSceneId === 'day1_screen_entry_invite'
                )
                || (
                    previousSceneId === 'day1_screen_entry_invite'
                    && nextSceneId === 'day1_takeover_capture_cursor'
                )
                || (
                    previousSceneId === 'day1_takeover_capture_cursor'
                    && nextSceneId === 'day1_takeover_return_control'
                )
                || (
                    previousSceneId === 'day2_tool_toggle_intro'
                    && this.isDay2AvatarToolsSceneId(nextSceneId)
                )
                || (
                    this.isDay2AvatarToolsSceneId(previousSceneId)
                    && this.isDay2AvatarToolsSceneId(nextSceneId)
                )
                || (
                    this.isDay2AvatarToolsSceneId(previousSceneId)
                    && this.isDay2GalgameSceneId(nextSceneId)
                )
                || (
                    this.isDay2GalgameSceneId(previousSceneId)
                    && this.isDay2GalgameSceneId(nextSceneId)
                )
                || (
                    this.isDay2WrapSceneId(previousSceneId)
                    && this.isDay2WrapSceneId(nextSceneId)
                )
            );
        },

        shouldPreserveIntroExternalizedChatCursor(scene) {
            return this.isAvatarFloatingInputIntroScene(scene);
        },

        setExternalizedChatCursorEffect(kind, effect, options) {
            if (
                !this.isHomeChatExternalized()
                || !this.interactionTakeover
                || typeof this.interactionTakeover.setExternalizedChatCursor !== 'function'
            ) {
                return false;
            }
            const normalizedKind = typeof kind === 'string' ? kind : '';
            const effectDurationMs = options && Number.isFinite(options.effectDurationMs)
                ? Math.max(0, Math.floor(options.effectDurationMs))
                : 0;
            const cursorOptions = {
                effect: effect || '',
                effectDurationMs: effectDurationMs,
                targetIndex: options && Number.isFinite(options.targetIndex)
                    ? Math.max(0, Math.floor(options.targetIndex))
                    : 0,
                freezePoint: !!(options && options.freezePoint === true)
            };
            if (options && Number.isFinite(options.durationMs)) {
                cursorOptions.durationMs = Math.max(0, Math.floor(options.durationMs));
            }
            if (normalizedKind) {
                this.rememberExternalizedChatCursorHandoffPoint(normalizedKind, cursorOptions.effect);
                this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            } else {
                this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            }
            this.interactionTakeover.setExternalizedChatCursor(normalizedKind, cursorOptions);
            return true;
        },

        clearExternalizedChatSpotlightOnly() {
            if (!this.isHomeChatExternalized() || !this.interactionTakeover) {
                return false;
            }
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.interactionTakeover.setExternalizedChatSpotlight('');
                return true;
            }
            return false;
        },

        clearExternalizedChatGuideTarget(options) {
            if (!this.isHomeChatExternalized() || !this.interactionTakeover) {
                return;
            }
            const shouldClearCursor = !!(options && options.clearCursor === true);
            const shouldPreservePcOverlayCursor = !!(options && options.preservePcOverlayCursor === true);
            if (shouldClearCursor && shouldPreservePcOverlayCursor) {
                this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            }
            if (
                shouldClearCursor
                && shouldPreservePcOverlayCursor
                && this.overlay
                && typeof this.overlay.getCursorPosition === 'function'
                && typeof this.overlay.syncCursorPosition === 'function'
            ) {
                const currentCursorPoint = this.overlay.getCursorPosition();
                if (
                    currentCursorPoint
                    && Number.isFinite(currentCursorPoint.x)
                    && Number.isFinite(currentCursorPoint.y)
                ) {
                    this.overlay.syncCursorPosition(currentCursorPoint.x, currentCursorPoint.y, true);
                }
            }
            if (typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                this.interactionTakeover.setExternalizedChatSpotlight('');
            }
            if (
                shouldClearCursor
                && typeof this.interactionTakeover.setExternalizedChatCursor === 'function'
            ) {
                this.interactionTakeover.setExternalizedChatCursor('', {
                    preservePcOverlayCursor: shouldPreservePcOverlayCursor
                });
                if (!shouldPreservePcOverlayCursor) {
                    this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
                }
            }
        },

        createAvatarFloatingUnionTarget(key, elements, options) {
            const targets = Array.isArray(elements) ? elements.filter(Boolean) : [];
            if (targets.length === 0) {
                return null;
            }
            if (targets.length === 1) {
                return targets[0];
            }
            return this.createUnionSpotlight(key, targets, Object.assign({
                padding: DEFAULT_SPOTLIGHT_PADDING,
                radius: 18
            }, options || {}));
        },

        resolveRegisteredAvatarFloatingSelector(selector) {
            const localSelectors = this.spotlightController.getLocalSelectors(selector);
            if (!Array.isArray(localSelectors) || localSelectors.length === 0) {
                return null;
            }
            for (let index = 0; index < localSelectors.length; index += 1) {
                const target = this.resolveElement(localSelectors[index]);
                if (target) {
                    if (
                        selector === 'chat-tool-toggle'
                        || selector === 'chat-avatar-tools'
                        || selector === 'chat-galgame'
                    ) {
                        this.applyChatAvatarToolButtonSpotlightHint(target);
                    }
                    return target;
                }
            }
            return null;
        },

        resolveAvatarFloatingSelector(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return null;
            }
            if (selector === 'chat-window' || selector === 'floating-buttons') {
                return this.getAvatarFloatingBaseTarget(selector);
            }
            const registeredTarget = this.resolveRegisteredAvatarFloatingSelector(selector);
            if (registeredTarget) {
                return registeredTarget;
            }
            if (selector === 'chat-input') {
                return this.getChatInputTarget();
            }
            if (selector === 'chat-capsule-input') {
                return this.getChatCapsuleInputTarget();
            }
            if (selector === 'chat-history-handle') {
                return this.resolveElement('#react-chat-window-root .compact-history-visibility-handle')
                    || this.resolveElement('.compact-history-visibility-handle');
            }
            if (selector === 'chat-tool-toggle') {
                const button = this.resolveElement('#react-chat-window-root .send-button-circle.compact-input-tool-toggle')
                    || this.resolveElement('.send-button-circle.compact-input-tool-toggle');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return null;
            }
            if (selector === 'chat-tools') {
                return this.resolveElement('#react-chat-window-root .composer-bottom-tools')
                    || this.resolveElement('#react-chat-window-root .composer-panel')
                    || this.getAvatarFloatingBaseTarget('chat-window');
            }
            if (selector === 'chat-avatar-tools') {
                const button = this.getVisibleChatComposerElement('.compact-input-tool-item-avatar .composer-emoji-btn')
                    || this.getVisibleChatComposerElement('.compact-input-tool-item-avatar')
                    || this.getVisibleChatComposerElement('.composer-emoji-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.getVisibleChatComposerElement('.composer-tool-menu')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            if (selector === 'chat-avatar-tool-items') {
                return this.createAvatarFloatingUnionTarget(
                    'chat-avatar-tool-items',
                    this.getChatAvatarToolMenuTargets()
                );
            }
            if (selector === 'chat-galgame') {
                const button = this.getVisibleChatComposerElement('.compact-input-tool-item-galgame')
                    || this.getVisibleChatComposerElement('.composer-galgame-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.getVisibleChatComposerElement('.composer-tool-menu')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            if (selector === 'chat-choice-slot') {
                return this.resolveElement('#react-chat-window-root .composer-choice-slot')
                    || this.resolveElement('#react-chat-window-root .composer-galgame-slot')
                    || this.resolveAvatarFloatingSelector('chat-tools');
            }
            return this.resolveElement(selector);
        },

        getMiniGameChoiceTargets(limit) {
            const maxTargets = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 3;
            if (maxTargets <= 0) {
                return [];
            }
            const choiceSlot = this.resolveElement(
                '#react-chat-window-root .composer-choice-slot[data-choice-source="mini_game_invite"]'
            );
            if (!choiceSlot || !this.isElementVisible(choiceSlot)) {
                return [];
            }
            return Array.from(choiceSlot.querySelectorAll('.composer-choice-option, .composer-galgame-option'))
                .filter((element, index, array) => element && array.indexOf(element) === index)
                .filter((element) => this.isElementVisible(element))
                .slice(0, maxTargets);
        },

        async tourPlainCircularTargets(targets, options) {
            const normalizedOptions = options || {};
            const items = this.applyPlainCircularSpotlightHints(targets, normalizedOptions.padding);
            if (items.length === 0) {
                return false;
            }
            this.setSceneExtraSpotlights(items);
            for (let index = 0; index < items.length; index += 1) {
                if (this.isStopping()) {
                    return false;
                }
                const moved = await this.moveCursorToElement(
                    items[index],
                    index === 0 ? (normalizedOptions.firstMoveMs || 560) : (normalizedOptions.moveMs || 420)
                );
                if (moved && !this.isStopping()) {
                    this.cursor.wobble();
                    await this.waitForSceneDelay(normalizedOptions.pauseMs || 180);
                }
            }
            return true;
        },

        async tourExternalizedChatTargets(kind, count, options) {
            const normalizedKind = typeof kind === 'string' ? kind : '';
            if (
                !this.isHomeChatExternalized()
                || !normalizedKind
                || !this.interactionTakeover
                || typeof this.interactionTakeover.setExternalizedChatSpotlight !== 'function'
                || typeof this.interactionTakeover.setExternalizedChatCursor !== 'function'
            ) {
                return false;
            }
            const normalizedOptions = options || {};
            const total = Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 3;
            const spotlightVariant = typeof normalizedOptions.spotlightVariant === 'string'
                ? normalizedOptions.spotlightVariant.trim()
                : '';
            this.clearHomeSpotlightsForExternalizedChat();
            this.interactionTakeover.setExternalizedChatSpotlight(normalizedKind, {
                variant: spotlightVariant
            });
            this.setHomePcCursorOutputSuppressedForExternalizedChat(true);
            this.hideHomeCursorForExternalizedChat();
            for (let index = 0; index < total; index += 1) {
                if (this.isStopping()) {
                    return false;
                }
                this.interactionTakeover.setExternalizedChatCursor(normalizedKind, {
                    effect: typeof normalizedOptions.effect === 'string' ? normalizedOptions.effect : 'move',
                    targetIndex: index
                });
                await this.waitForSceneDelay(index === 0
                    ? (normalizedOptions.firstPauseMs || 560)
                    : (normalizedOptions.pauseMs || 420));
            }
            return true;
        },

        async tourAvatarToolMenuItems() {
            if (this.isHomeChatExternalized()) {
                return this.tourExternalizedChatTargets('avatar-tool-items', 3, {
                    effect: 'move',
                    firstPauseMs: 560,
                    pauseMs: 420
                });
            }
            return this.tourPlainCircularTargets(this.getChatAvatarToolMenuTargets(3), {
                padding: 6,
                firstMoveMs: 560,
                moveMs: 420,
                pauseMs: 180
            });
        },

        async tourMiniGameChoiceButtons() {
            if (this.isHomeChatExternalized()) {
                return false;
            }
            const targets = this.getMiniGameChoiceTargets(3);
            if (targets.length > 0) {
                this.overlay.clearActionSpotlight();
                this.overlay.clearPersistentSpotlight();
            }
            return this.tourPlainCircularTargets(targets, {
                padding: 6,
                firstMoveMs: 560,
                moveMs: 420,
                pauseMs: 180
            });
        },

        async runDay3GalgameWheelDragScene(scene, primaryTarget) {
            this.setCompactToolFanOpen(true, 'avatar-floating-guide-galgame-tool-fan-open');
            await this.waitForSceneDelay(120);
            const dragArcFraction = 1 / 5;
            const dragSettleWaitMs = 420;
            const dragRotateDirection = -1;
            const dragRotateDelayMs = Math.round(dragSettleWaitMs * 0.45);
            const rotateReason = 'avatar-floating-guide-galgame-drag';
            const buildDay3GalgameWheelArcPoints = (targetElement, fraction, direction, stepCount = 8) => {
                const targetRect = this.getElementRect(targetElement);
                const fan = this.resolveElement('#react-chat-window-root .compact-input-tool-fan')
                    || this.resolveElement('.compact-input-tool-fan');
                const fanRect = fan && typeof fan.getBoundingClientRect === 'function'
                    ? fan.getBoundingClientRect()
                    : null;
                if (!targetRect || !fanRect || fanRect.width <= 0 || fanRect.height <= 0) {
                    return [];
                }
                const fanStyle = window.getComputedStyle ? window.getComputedStyle(fan) : null;
                const readFanPixelVar = (name, fallback) => {
                    const rawValue = fanStyle ? String(fanStyle.getPropertyValue(name) || '').trim() : '';
                    const parsedValue = Number.parseFloat(rawValue);
                    return Number.isFinite(parsedValue) ? parsedValue : fallback;
                };
                const center = {
                    x: fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', 116),
                    y: fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', 116)
                };
                const start = {
                    x: targetRect.left + targetRect.width / 2,
                    y: targetRect.top + targetRect.height / 2
                };
                const radius = Math.hypot(start.x - center.x, start.y - center.y);
                if (!Number.isFinite(radius) || radius < 4) {
                    return [];
                }
                const startAngle = Math.atan2(start.y - center.y, start.x - center.x);
                const totalAngle = (direction < 0 ? -1 : 1) * Math.PI * 2 * Math.max(0, Math.min(1, fraction));
                const count = Math.max(2, Math.floor(stepCount));
                const points = [];
                for (let index = 1; index <= count; index += 1) {
                    const progress = index / count;
                    const angle = startAngle + totalAngle * progress;
                    points.push({
                        x: center.x + Math.cos(angle) * radius,
                        y: center.y + Math.sin(angle) * radius
                    });
                }
                return points;
            };
            const rotateWheelAfterDragThreshold = async () => {
                const waited = await this.waitForSceneDelay(dragRotateDelayMs);
                if (!waited || this.isStopping()) {
                    return false;
                }
                this.rotateCompactToolWheelForGuide(dragRotateDirection, 1, rotateReason);
                return true;
            };

            if (this.isHomeChatExternalized()) {
                this.setExternalizedChatCursorEffect('galgame', 'move');
                await this.waitForExternalizedChatCursorMove(
                    scene && scene.id || 'day2_galgame_entry',
                    1800
                );
                if (this.isStopping()) {
                    return false;
                }
                if (
                    this.interactionTakeover
                    && typeof this.interactionTakeover.arcExternalizedChatCursor === 'function'
                ) {
                    this.interactionTakeover.arcExternalizedChatCursor('galgame', {
                        direction: dragRotateDirection,
                        fraction: dragArcFraction,
                        durationMs: dragSettleWaitMs,
                        effect: 'click',
                        effectDurationMs: dragSettleWaitMs
                    });
                }
                const rotated = await rotateWheelAfterDragThreshold();
                if (!rotated) {
                    return false;
                }
                const remainingDragWaitMs = Math.max(0, dragSettleWaitMs - dragRotateDelayMs);
                await this.waitForSceneDelay(remainingDragWaitMs + 80);
                await this.waitForSceneDelay(260);
                this.setExternalizedChatCursorEffect('galgame', 'click', {
                    durationMs: 0,
                    effectDurationMs: DEFAULT_CURSOR_CLICK_VISIBLE_MS
                });
                await this.waitForExternalizedChatCursorMove(
                    scene && scene.id || 'day2_galgame_entry',
                    DEFAULT_CURSOR_CLICK_VISIBLE_MS + 500
                );
                return true;
            }

            const target = primaryTarget || await this.resolveAvatarFloatingTarget(scene, 'primary');
            const rect = this.getElementRect(target);
            if (!rect) {
                return false;
            }
            const arcPoints = buildDay3GalgameWheelArcPoints(
                target,
                dragArcFraction,
                dragRotateDirection
            );
            if (arcPoints.length === 0) {
                return false;
            }
            this.cursor.click(dragSettleWaitMs);
            let dragFinished = false;
            let dragSucceeded = false;
            const movePromise = this.cursor.moveCursorAlongPoints(arcPoints, {
                durationMs: dragSettleWaitMs,
                effect: 'click',
                effectDurationMs: dragSettleWaitMs,
                pauseCheck: () => this.scenePausedForResistance,
                cancelCheck: () => this.isStopping()
            }).then((moved) => {
                dragFinished = true;
                dragSucceeded = !!moved;
                return moved;
            }, (error) => {
                dragFinished = true;
                dragSucceeded = false;
                throw error;
            });
            const rotatePromise = (async () => {
                const waited = await this.waitForSceneDelay(dragRotateDelayMs);
                if (
                    !waited
                    || this.isStopping()
                    || (dragFinished && !dragSucceeded)
                ) {
                    return false;
                }
                this.rotateCompactToolWheelForGuide(dragRotateDirection, 1, rotateReason);
                return true;
            })();
            const moved = await movePromise;
            const rotated = await rotatePromise;
            if (!moved || this.isStopping()) {
                return false;
            }
            if (!rotated) {
                return false;
            }
            await this.waitForSceneDelay(260);
            const finalTarget = await this.resolveDay3GalgameWheelSlotTarget(1, 720)
                || await this.resolveAvatarFloatingTarget(scene, 'primary');
            if (finalTarget) {
                await this.moveCursorToElement(finalTarget, 0, {
                    exactDuration: true
                });
                this.cursor.click(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
            }
            return true;
        },

        async resolveDay3GalgameWheelSlotTarget(slot, timeoutMs) {
            const normalizedSlot = Number.isFinite(Number(slot)) ? String(Math.floor(Number(slot))) : '';
            const current = this.getVisibleChatComposerElement('.compact-input-tool-item-galgame')
                || this.getVisibleChatComposerElement('.composer-galgame-btn');
            if (!current || !current.hasAttribute('data-compact-tool-wheel-slot')) {
                return current || null;
            }
            if (current.getAttribute('data-compact-tool-wheel-slot') === normalizedSlot) {
                this.applyChatAvatarToolButtonSpotlightHint(current);
                return current;
            }
            const selector = '.compact-input-tool-item-galgame[data-compact-tool-wheel-slot="' + normalizedSlot + '"]';
            const target = await this.waitForElement(() => {
                const button = this.getVisibleChatComposerElement(selector);
                return button || null;
            }, Number.isFinite(timeoutMs) ? Math.max(0, Math.floor(timeoutMs)) : 720);
            if (target) {
                this.applyChatAvatarToolButtonSpotlightHint(target);
                return target;
            }
            return current;
        },

        async resolveAvatarFloatingTarget(scene, role) {
            const targetKey = role === 'secondary' ? scene.secondary : scene.target;
            if (!targetKey) {
                return null;
            }
            if (targetKey === 'agent-master') {
                return this.getAgentToggleElement('agent-master') || this.resolveElement('#${p}-toggle-agent-master');
            }
            if (targetKey === 'agent-capabilities') {
                return this.createAvatarFloatingUnionTarget(
                    scene.id + '-capabilities',
                    this.getAvatarFloatingAgentCapabilityTargets()
                );
            }
            if (targetKey === 'chat-avatar-tools') {
                const button = await this.getChatComposerToolButton('.compact-input-tool-item-avatar .composer-emoji-btn')
                    || await this.getChatComposerToolButton('.compact-input-tool-item-avatar')
                    || await this.getChatComposerToolButton('.composer-emoji-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.resolveAvatarFloatingSelector(targetKey);
            }
            if (targetKey === 'chat-galgame') {
                const button = await this.getChatComposerToolButton('.compact-input-tool-item-galgame')
                    || await this.getChatComposerToolButton('.composer-galgame-btn');
                if (button) {
                    this.applyChatAvatarToolButtonSpotlightHint(button);
                    return button;
                }
                return this.resolveAvatarFloatingSelector(targetKey);
            }
            if (typeof targetKey === 'string' && targetKey.indexOf('settings-sidepanel:') === 0) {
                const type = targetKey.split(':')[1] || '';
                if (scene && scene.deferSettingsSidePanelUntilCursorClick === true) {
                    return this.getAvatarFloatingSidePanel(type);
                }
                return this.getAvatarFloatingSidePanel(type) || await this.ensureAvatarFloatingSettingsSidePanel(type);
            }
            if (scene && scene.id === 'day4_model_lock' && targetKey === '#${p}-lock-icon') {
                return this.getDay4LockButtonSpotlightTarget();
            }
            if (targetKey === '.mic-option') {
                return this.resolveElement('.mic-option') || this.resolveElement('#${p}-popup-mic');
            }
            return this.resolveAvatarFloatingSelector(targetKey);
        },

        async resolveAvatarFloatingPersistent(scene, options) {
            const normalizedOptions = options || {};
            if (scene && scene.id === 'day6_agent_status_master') {
                return null;
            }
            const persistent = typeof scene.persistent === 'string' ? scene.persistent : '';
            if (persistent) {
                const target = this.resolveAvatarFloatingSelector(persistent);
                if (target) {
                    return target;
                }
            }
            if (normalizedOptions.fallbackToChatWindow === true) {
                return this.getAvatarFloatingBaseTarget('chat-window');
            }
            return null;
        },

        async applyAvatarFloatingSettledCleanupHighlight(scene) {
            const normalizedScene = scene || {};
            const highlightConfig = {
                key: (normalizedScene.id || 'scene') + '-settled',
                persistent: await this.resolveAvatarFloatingPersistent(normalizedScene, {
                    fallbackToChatWindow: false
                }),
                primary: await this.resolveAvatarFloatingTarget(normalizedScene, 'primary'),
                secondary: await this.resolveAvatarFloatingTarget(normalizedScene, 'secondary')
            };
            this.applyAvatarFloatingPersistenceOverride(highlightConfig, normalizedScene.id);
            this.applyGuideHighlights(highlightConfig);
        },

        applyAvatarFloatingSceneSpotlightVariant(scene, target) {
            const variant = scene && typeof scene.spotlightVariant === 'string'
                ? scene.spotlightVariant.trim()
                : '';
            if (!variant || !target) {
                return;
            }
            this.setSpotlightVariantHints([{
                element: target,
                variant
            }]);
        },

        async prepareAvatarFloatingScene(scene, options) {
            const operation = typeof scene.operation === 'string' ? scene.operation : '';
            const deferSettingsSidePanelUntilCursorClick = !!(
                scene
                && scene.deferSettingsSidePanelUntilCursorClick === true
            );
            const preserveExternalizedChatGuideTarget = !!(
                (options && options.preserveExternalizedChatGuideTarget)
                || (scene && scene.preserveExternalizedChatGuideTarget === true)
            );
            if (scene.cleanupBefore) {
                if (preserveExternalizedChatGuideTarget) {
                    this.closeChatToolPopover();
                } else {
                    await this.closeAvatarFloatingGuidePanels();
                }
            }
            if (operation === 'show-task-hud') {
                const existingHud = document.getElementById('agent-task-hud');
                this.avatarFloatingGuideTemporaryHudWasVisible = !!(
                    existingHud && existingHud.style.display !== 'none' && this.isElementVisible(existingHud)
                );
                if (window.AgentHUD && typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                    window.AgentHUD.showAgentTaskHUD();
                    this.avatarFloatingGuideTemporaryHudShown = true;
                    if (typeof window.AgentHUD.expandAgentTaskHUD === 'function') {
                        window.AgentHUD.expandAgentTaskHUD();
                    }
                } else if (window.AgentHUD && typeof window.AgentHUD.createAgentTaskHUD === 'function') {
                    const hud = window.AgentHUD.createAgentTaskHUD();
                    if (hud) {
                        hud.style.display = 'flex';
                        hud.style.opacity = '1';
                        this.avatarFloatingGuideTemporaryHudShown = true;
                        if (typeof window.AgentHUD.expandAgentTaskHUD === 'function') {
                            window.AgentHUD.expandAgentTaskHUD();
                        }
                    }
                }
                await this.waitForElement(() => {
                    const hud = document.getElementById('agent-task-hud');
                    return hud && this.isElementVisible(hud) ? hud : null;
                }, 1200);
                return;
            }
            if (operation.indexOf('show-agent-sidepanel:') === 0) {
                const parts = operation.split(':');
                await this.openAgentPanel();
                await this.ensureAvatarFloatingAgentSidePanel(parts[1] || 'user-plugin');
                return;
            }
            if (
                operation.indexOf('show-settings-sidepanel:') === 0
                && !deferSettingsSidePanelUntilCursorClick
            ) {
                await this.ensureAvatarFloatingSettingsSidePanel(operation.split(':')[1] || '');
            }
            if (operation === 'day4-animation-distance-showcase') {
                await this.ensureAvatarFloatingSettingsSidePanel('animation-settings');
            }
            if (operation.indexOf('show-settings-menu:') === 0) {
                await this.ensureSettingsMenuVisible(operation.split(':')[1] || '');
            }
            if (operation === 'open-avatar-tool-menu') {
                this.setCompactToolFanOpen(true, 'avatar-floating-guide-prepare-avatar-tools');
                this.setChatAvatarToolMenuOpen(false, 'avatar-floating-guide-prepare');
            }
            if (
                operation === 'toggle-avatar-tool-after-narration'
                || operation === 'show-avatar-tools-then-hide-after-narration'
            ) {
                this.setCompactToolFanOpen(true, 'avatar-floating-guide-prepare-tool-fan');
            }
            if (operation === 'day3-settings-detail') {
                await this.closeAgentPanel().catch(() => {});
                await this.openSettingsPanel();
            }
            if (operation === 'cleanup') {
                await this.closeAvatarFloatingGuidePanels({
                    preserveExternalizedChatGuideTarget
                });
            }
        },

        async runDay6PluginOpenAgentPanelFlow(scene) {
            const sceneId = scene && scene.id ? scene.id : 'day6_agent_status_master';
            const scaleSceneMs = this.createSceneScaler(scene && scene.voiceKey);
            const guardFailed = () => this.isStopping();
            this.revealAvatarFloatingToolbarForGuideInteraction(sceneId);
            const catPawButton = await this.waitForVisibleTarget([
                () => this.getFloatingButtonShell(this.getFallbackFloatingButton('agent')),
                () => this.getFallbackFloatingButton('agent'),
                () => this.getFloatingButtonShell(this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw))),
                () => this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw))
            ], 2200);
            if (!catPawButton || guardFailed()) {
                return false;
            }

            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.applyGuideHighlights({
                key: sceneId + '-cat-paw',
                primary: catPawButton
            });
            if (!(await this.waitForSceneDelay(DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS)) || guardFailed()) {
                return false;
            }
            await this.moveAvatarFloatingCursor(Object.assign({}, scene || {}, {
                id: sceneId,
                cursorAction: 'move',
                cursorMoveDurationMs: scaleSceneMs(DAY6_PLUGIN_AGENT_PANEL_CURSOR_MOVE_MS, 2100, 5200)
            }), catPawButton, null, null, {
                targetPointOffset: { y: DAY6_PLUGIN_CAT_PAW_CURSOR_OFFSET_Y },
                clampTargetPointToRect: true,
                targetPointClampInsetPx: 4
            });
            if (guardFailed()) {
                return false;
            }
            const opened = await this.runActionWithCursorClickExact(
                scaleSceneMs(DAY6_PLUGIN_AGENT_PANEL_CLICK_VISIBLE_MS, 480, 1200),
                () => this.openAgentPanel()
            );
            if (!opened || guardFailed()) {
                return false;
            }
            this.day6PluginDashboardPreview = Object.assign({}, this.day6PluginDashboardPreview || {}, {
                catPawButton: catPawButton
            });
            return true;
        },

        async runDay6PluginOpenManagementPanelFlow(scene) {
            const sceneId = scene && scene.id ? scene.id : 'day6_plugin_side_panel';
            const scaleSceneMs = this.createSceneScaler(scene && scene.voiceKey);
            const guardFailed = () => this.isStopping();
            const agentPanelOpened = await this.openAgentPanel();
            if (!agentPanelOpened || guardFailed()) {
                return false;
            }
            const refreshUserPluginHighlight = (target) => {
                if (!target || guardFailed()) {
                    return false;
                }
                this.applyGuideHighlights({
                    key: sceneId + '-user-plugin',
                    primary: target
                });
                return true;
            };
            const refreshManagementHighlight = (button) => {
                if (!button || guardFailed()) {
                    return null;
                }
                this.clearVirtualSpotlight('plugin-management-entry');
                const spotlightTarget = this.createPluginManagementEntrySpotlight(button) || button;
                this.applyGuideHighlights({
                    key: sceneId + '-management-panel',
                    primary: spotlightTarget
                });
                return spotlightTarget;
            };
            const userPluginToggle = await this.waitForElement(() => {
                const toggle = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggle) ? toggle : null;
            }, 1800);
            if (!userPluginToggle || guardFailed()) {
                return false;
            }
            if (!(await this.waitForStableElementRect(userPluginToggle, 760)) || guardFailed()) {
                return false;
            }
            if (!refreshUserPluginHighlight(userPluginToggle)) {
                return false;
            }
            if (!(await this.waitForSceneDelay(DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS)) || guardFailed()) {
                return false;
            }
            const userPluginMovePromise = this.moveCursorToTrackedElement(
                userPluginToggle,
                scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS, 840, 2100),
                {
                    exactDuration: true,
                    recheckDelayMs: 120,
                    settleDelayMs: 40
                }
            );
            const movedToUserPlugin = await userPluginMovePromise;
            if (!movedToUserPlugin || guardFailed()) {
                return false;
            }
            const sidePanelShown = await this.runActionWithCursorClickExact(
                scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS, 360, 900),
                () => this.ensureAvatarFloatingAgentSidePanel('user-plugin')
            );
            if (!sidePanelShown || guardFailed()) {
                return false;
            }

            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                DAY6_PLUGIN_SIDE_PANEL_ACTION_TIMEOUT_MS
            );
            if (!managementButton || guardFailed()) {
                return false;
            }
            if (!(await this.waitForStableElementRect(managementButton, 760)) || guardFailed()) {
                return false;
            }
            this.applyGuideHighlights({
                key: sceneId + '-clear-user-plugin',
                primary: null
            });
            let managementSpotlightTarget = refreshManagementHighlight(managementButton);
            if (!managementSpotlightTarget || guardFailed()) {
                return false;
            }
            if (!(await this.moveCursorToTrackedElement(
                managementButton,
                scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS, 840, 2100),
                {
                    exactDuration: true,
                    recheckDelayMs: 120,
                    settleDelayMs: 40
                }
            )) || guardFailed()) {
                return false;
            }
            managementSpotlightTarget = refreshManagementHighlight(managementButton);
            if (!managementSpotlightTarget || guardFailed()) {
                return false;
            }
            if (!this.isCursorAlignedWithElement(managementButton, 5)) {
                if (!(await this.realignCursorToAgentSidePanelAction(
                    'agent-user-plugin',
                    'management-panel',
                    220
                )) || guardFailed()) {
                    return false;
                }
                managementSpotlightTarget = refreshManagementHighlight(managementButton);
                if (!managementSpotlightTarget || guardFailed()) {
                    return false;
                }
            }
            const managementOpenResult = await this.runActionWithCursorClickExact(
                scaleSceneMs(DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS, 360, 900),
                async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true,
                        source: 'avatar-floating-guide',
                        sceneId: sceneId
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                }
            );
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            if (!agentPanelActionOpened || guardFailed()) {
                return false;
            }

            const pluginDashboardWindow = hadPluginDashboard
                ? existingPluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, DAY6_PLUGIN_SIDE_PANEL_DASHBOARD_WAIT_MS);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed || guardFailed()) {
                return true;
            }
            this.day6PluginDashboardPreview = Object.assign({}, this.day6PluginDashboardPreview || {}, {
                pluginDashboardWindow: pluginDashboardWindow,
                pluginDashboardWindowCreatedByGuide: !hadPluginDashboard,
                userPluginToggle: userPluginToggle,
                managementButton: managementButton
            });
            return true;
        },

        async runDay6PluginDashboardHandoffFlow(scene, narrationStartedAt) {
            const guardFailed = () => this.isStopping();
            const previewState = this.day6PluginDashboardPreview || {};
            const homeCursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const pluginDashboardWindow = (
                previewState.pluginDashboardWindow
                && !previewState.pluginDashboardWindow.closed
            )
                ? previewState.pluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 1800);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed) {
                const cleanupCompleted = await this.cleanupDay6PluginDashboardPostNarration(
                    previewState,
                    homeCursorPosition,
                    this.sceneRunId
                );
                this.day6PluginDashboardPreview = null;
                return cleanupCompleted && !guardFailed();
            }
            if (guardFailed()) {
                return false;
            }
            this.pluginDashboardWindowCreatedByGuide = previewState.pluginDashboardWindowCreatedByGuide !== false;

            this.hideHomeCursorForExternalizedChat();

            const voiceKey = scene && scene.voiceKey ? scene.voiceKey : '';
            const text = scene && scene.text ? scene.text : '';
            const audioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                ? this.voiceQueue.resolveGuideAudioSrc(voiceKey)
                : '';
            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const dashboardNarrationStartedAtMs = Number.isFinite(narrationStartedAt) ? narrationStartedAt : Date.now();
            const elapsedNarrationMs = Math.max(0, Date.now() - dashboardNarrationStartedAtMs);
            await this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow, {
                line: text,
                closeOnDone: false,
                narrationDurationMs: narrationDurationMs,
                voiceKey: voiceKey,
                audioUrl: audioUrl,
                narrationStartedAtMs: dashboardNarrationStartedAtMs
            }, {
                narrationDurationMs,
                elapsedNarrationMs
            }).catch(() => false);

            const cleanupCompleted = await this.cleanupDay6PluginDashboardPostNarration(
                previewState,
                homeCursorPosition,
                this.sceneRunId
            );
            this.day6PluginDashboardPreview = null;
            if (!cleanupCompleted || guardFailed()) {
                return false;
            }
            return true;
        },

        async cleanupDay6PluginDashboardPostNarration(previewState, homeCursorPosition, sceneRunId) {
            const normalizedPreviewState = previewState || {};
            try {
                await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');
                this.collapseAgentSidePanel('agent-user-plugin');
                this.clearVirtualSpotlight('plugin-management-entry');
                this.stopHoverElement(normalizedPreviewState.userPluginToggle || null);
                await this.closeAgentPanel().catch(() => {});
                const homeReady = await this.waitForHomeMainUIReady(3600);
                if (!homeReady) {
                    return false;
                }
                if (
                    homeCursorPosition
                    && this.sceneRunId === sceneRunId
                    && !this.isStopping()
                ) {
                    this.cursor.showAt(homeCursorPosition.x, homeCursorPosition.y);
                }
                return true;
            } catch (error) {
                console.warn('[YuiGuide] Day 6 插件管理后台收尾失败:', error);
                return false;
            }
        },

        async runDay6PluginSidePanelFlow(scene, narrationStartedAt) {
            const sceneId = scene && scene.id ? scene.id : 'day6_plugin_side_panel';
            const guardFailed = () => this.isStopping();
            const agentPanel = () => this.resolveAvatarFloatingSelector('#${p}-popup-agent');
            const catPawButton = this.getFloatingButtonShell(this.getFallbackFloatingButton('agent'))
                || this.getFallbackFloatingButton('agent')
                || this.queryDocumentSelector(this.expandSelector(TAKEOVER_CAPTURE_SELECTORS.catPaw));
            if (!catPawButton || guardFailed()) {
                return false;
            }

            this.setSpotlightGeometryHint(catPawButton, {
                padding: 4,
                geometry: 'circle'
            });
            this.applyGuideHighlights({
                key: sceneId + '-cat-paw',
                primary: catPawButton
            });
            if (!(await this.moveCursorToElement(catPawButton, 760)) || guardFailed()) {
                return false;
            }
            const opened = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                () => this.openAgentPanel()
            );
            if (!opened || guardFailed()) {
                return false;
            }

            const userPluginToggle = await this.waitForElement(() => {
                const toggle = this.getAgentToggleElement('agent-user-plugin');
                return this.getElementRect(toggle) ? toggle : null;
            }, 1800);
            if (!userPluginToggle || guardFailed()) {
                return false;
            }
            this.applyGuideHighlights({
                key: sceneId + '-user-plugin',
                persistent: agentPanel(),
                primary: userPluginToggle
            });
            if (!(await this.moveCursorToElement(userPluginToggle, 420)) || guardFailed()) {
                return false;
            }
            const sidePanelShown = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                () => this.ensureAvatarFloatingAgentSidePanel('user-plugin')
            );
            if (!sidePanelShown || guardFailed()) {
                return false;
            }

            const managementButton = await this.ensureAgentSidePanelActionVisible(
                'agent-user-plugin',
                'management-panel',
                2600
            );
            if (!managementButton || guardFailed()) {
                return false;
            }
            const managementSpotlightTarget = this.createPluginManagementEntrySpotlight(managementButton) || managementButton;
            this.applyGuideHighlights({
                key: sceneId + '-management-panel',
                persistent: agentPanel(),
                primary: managementSpotlightTarget
            });
            if (!(await this.moveCursorToElement(managementButton, 420)) || guardFailed()) {
                return false;
            }
            const managementOpenResult = await this.runActionWithCursorClick(
                DEFAULT_CURSOR_CLICK_VISIBLE_MS,
                async () => {
                    const existingPluginDashboardWindow = await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 120);
                    const hadPluginDashboard = !!(existingPluginDashboardWindow && !existingPluginDashboardWindow.closed);
                    const agentPanelActionOpened = await this.clickAgentSidePanelAction('agent-user-plugin', 'management-panel', {
                        keepMainUIVisible: true,
                        source: 'avatar-floating-guide',
                        sceneId: sceneId
                    });
                    return {
                        existingPluginDashboardWindow,
                        hadPluginDashboard,
                        agentPanelActionOpened
                    };
                }
            );
            const hadPluginDashboard = !!(managementOpenResult && managementOpenResult.hadPluginDashboard);
            const existingPluginDashboardWindow = managementOpenResult && managementOpenResult.existingPluginDashboardWindow;
            const agentPanelActionOpened = !!(managementOpenResult && managementOpenResult.agentPanelActionOpened);
            if (!agentPanelActionOpened || guardFailed()) {
                return false;
            }

            const pluginDashboardWindow = hadPluginDashboard
                ? existingPluginDashboardWindow
                : await this.waitForOpenedWindow(PLUGIN_DASHBOARD_WINDOW_NAME, 1800);
            if (!pluginDashboardWindow || pluginDashboardWindow.closed || guardFailed()) {
                return true;
            }
            this.pluginDashboardWindowCreatedByGuide = !hadPluginDashboard;

            const homeCursorPosition = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            this.hideHomeCursorForExternalizedChat();

            const voiceKey = scene && scene.voiceKey ? scene.voiceKey : '';
            const text = scene && scene.text ? scene.text : '';
            const audioUrl = this.voiceQueue && typeof this.voiceQueue.resolveGuideAudioSrc === 'function'
                ? this.voiceQueue.resolveGuideAudioSrc(voiceKey)
                : '';
            const narrationDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const dashboardNarrationStartedAtMs = Number.isFinite(narrationStartedAt) ? narrationStartedAt : Date.now();
            const elapsedNarrationMs = Math.max(0, Date.now() - dashboardNarrationStartedAtMs);
            await this.waitForPluginDashboardPerformanceUntilNarrationBoundary(pluginDashboardWindow, {
                line: text,
                closeOnDone: false,
                narrationDurationMs: narrationDurationMs,
                voiceKey: voiceKey,
                audioUrl: audioUrl,
                narrationStartedAtMs: dashboardNarrationStartedAtMs
            }, {
                narrationDurationMs,
                elapsedNarrationMs
            }).catch(() => false);

            await this.closePluginDashboardWindowIfCreatedByGuide('Day 6 插件管理预览完成');
            this.collapseAgentSidePanel('agent-user-plugin');
            this.clearVirtualSpotlight('plugin-management-entry');
            this.stopHoverElement(userPluginToggle);
            await this.closeAgentPanel().catch(() => {});
            const homeReady = await this.waitForHomeMainUIReady(3600);
            if (!homeReady || guardFailed()) {
                return false;
            }
            if (homeCursorPosition) {
                this.cursor.showAt(homeCursorPosition.x, homeCursorPosition.y);
            }
            return true;
        },

        async runDay4AnimationDistanceShowcase(scene, narrationStartedAt) {
            const durationMs = this.getAvatarFloatingNarrationDurationMs(scene.voiceKey || '', scene.text || '');
            const cueMs = clamp(Math.round(durationMs * 0.48), 2600, Math.max(2600, durationMs - 1800));
            const elapsedMs = Number.isFinite(narrationStartedAt)
                ? Math.max(0, Date.now() - narrationStartedAt)
                : 0;
            const waitMs = Math.max(0, cueMs - elapsedMs);
            if (!(await this.waitForSceneDelay(waitMs))) {
                return false;
            }
            if (this.isStopping()) {
                return false;
            }

            await this.closeAvatarFloatingGuidePanels();
            if (this.isStopping()) {
                return false;
            }

            const lockButton = this.resolveElement('#${p}-lock-icon');
            if (lockButton && this.isElementVisible(lockButton)) {
                this.applyGuideHighlights({
                    key: 'day4-animation-distance-lock',
                    primary: lockButton
                });
                await this.moveCursorToElement(lockButton, 680);
                this.cursor.wobble();
                await this.waitForSceneDelay(620);
            }
            if (this.isStopping()) {
                return false;
            }

            const goodbyeButton = this.resolveElement('#${p}-btn-goodbye');
            const returnButton = this.resolveElement('#${p}-btn-return');
            if (goodbyeButton && this.isElementVisible(goodbyeButton)) {
                this.applyGuideHighlights({
                    key: 'day4-animation-distance-goodbye',
                    primary: goodbyeButton,
                    secondary: returnButton && this.isElementVisible(returnButton) ? returnButton : null
                });
                await this.moveCursorToElement(goodbyeButton, 720);
                this.cursor.wobble();
                await this.waitForSceneDelay(720);
            }
            return true;
        },

        async playDay4ChatSettingsScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4ChatSettingsScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async playDay4ModelBehaviorScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4ModelBehaviorScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async playDay4GazeFollowScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4GazeFollowScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async playDay4PrivacyModeScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay4PrivacyModeScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async playDay5CharacterSettingsScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay5CharacterSettingsScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async playDay5CharacterPanicScene(scene, sceneRunId, previousSceneId, index, total) {
            return this.settingsTourFlow.playDay5CharacterPanicScene(scene, {
                sceneRunId,
                previousSceneId,
                index,
                total
            });
        },

        async runAvatarFloatingSceneOperation(scene, primaryTarget, narrationStartedAt, narrationPromise, operationContext) {
            return this.operationRegistry.run(scene, primaryTarget, narrationStartedAt, narrationPromise, operationContext);
        },

        closeChatToolPopover() {
            this.setCompactToolFanOpen(false, 'avatar-floating-guide-close-tool-fan');
            let closed = this.setChatAvatarToolMenuOpen(false, 'avatar-floating-guide-close-avatar-tool-menu');
            if (!closed) {
                const activeToolButton = this.resolveElement('#react-chat-window-root .composer-emoji-btn.is-active');
                if (activeToolButton && typeof activeToolButton.click === 'function') {
                    activeToolButton.click();
                    closed = true;
                }
                const activeOverflowButton = this.resolveElement('#react-chat-window-root .composer-overflow-btn.is-active');
                if (activeOverflowButton && typeof activeOverflowButton.click === 'function') {
                    activeOverflowButton.click();
                    closed = true;
                }
            }
            const popover = this.resolveElement('#composer-tool-popover');
            const overflowPopover = this.resolveElement('#react-chat-window-root .composer-overflow-popover');
            if (!popover && !overflowPopover) {
                return closed;
            }
            return closed;
        },

        getAvatarFloatingNarrationDurationMs(voiceKey, text) {
            const configuredDurationMs = this.getGuideVoiceDurationMs(voiceKey || '', resolveGuideLocale());
            if (configuredDurationMs > 0) {
                return configuredDurationMs;
            }
            return 0;
        },

        async playAvatarFloatingPetalTransitionAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt, cueWindowMs) {
            return this.petalTransitionController.playAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt, cueWindowMs);
        },

        rememberAvatarFloatingSceneCursorAnchor(sceneId, element) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            const rect = this.getElementRect(element);
            if (!normalizedSceneId || !rect) {
                return;
            }
            this.rememberAvatarFloatingSceneCursorAnchorPoint(normalizedSceneId, {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            });
        },

        rememberAvatarFloatingSceneCursorAnchorPoint(sceneId, point) {
            this.cursorAnchorStore.rememberScenePoint(sceneId, point);
        },

        getAvatarFloatingSceneCursorAnchor(sceneIds) {
            return this.cursorAnchorStore.getScenePoint(sceneIds);
        },

        resolveManagedSceneCursorAnchorPoint(previousSceneId) {
            return this.getAvatarFloatingSceneCursorAnchor(previousSceneId);
        },

        resolveAvatarFloatingCursorStartPoint(scene, targets, previousSceneId) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            const explicitStartTargets = [];
            if (sceneId === 'day2_screen_entry') {
                explicitStartTargets.push(this.getAvatarFloatingIntroSpotlightTarget({ id: 'day2_intro_context' }));
            } else if (sceneId === 'day2_wrap_intro') {
                const previousScreenAnchor = this.getAvatarFloatingSceneCursorAnchor([
                    'day2_screen_entry_invite',
                    'day2_screen_entry'
                ]);
                if (previousScreenAnchor) {
                    return previousScreenAnchor;
                }
                explicitStartTargets.push(this.resolveAvatarFloatingSelector('#${p}-btn-screen'));
            } else if (sceneId === 'day2_avatar_tools') {
                explicitStartTargets.push(this.resolveAvatarFloatingSelector('chat-tool-toggle'));
            }

            if (sceneId === 'day1_takeover_return_control') {
                const keyboardToggle = this.getAgentToggleElement('agent-keyboard');
                const keyboardRect = this.getElementRect(keyboardToggle);
                if (keyboardRect) {
                    return {
                        x: keyboardRect.left + keyboardRect.width / 2,
                        y: keyboardRect.top + keyboardRect.height / 2
                    };
                }
                const keyboardControlAnchor = this.getAvatarFloatingSceneCursorAnchor('day1_takeover_capture_cursor');
                if (keyboardControlAnchor) {
                    return keyboardControlAnchor;
                }
            }

            for (let index = 0; index < explicitStartTargets.length; index += 1) {
                const rect = this.getElementRect(explicitStartTargets[index]);
                if (rect) {
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    };
                }
            }

            const previousSceneAnchor = this.getAvatarFloatingSceneCursorAnchor(previousSceneId);
            if (previousSceneAnchor) {
                return previousSceneAnchor;
            }

            if (sceneId === 'day2_screen_entry') {
                const externalizedChatAnchor = this.getExternalizedChatCursorAnchorPoint(30000);
                if (externalizedChatAnchor) {
                    return externalizedChatAnchor;
                }
                const chatProxyAnchor = this.getAvatarFloatingChatProxyAnchorPoint();
                if (chatProxyAnchor) {
                    return chatProxyAnchor;
                }
            }

            const targetList = Array.isArray(targets) ? targets : [];
            for (let index = 0; index < targetList.length; index += 1) {
                const rect = this.getElementRect(targetList[index]);
                if (rect) {
                    return {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    };
                }
            }
            return null;
        },

        getAvatarFloatingChatProxyAnchorPoint() {
            const chatTarget = this.getChatIntroActivationTarget()
                || this.getChatWindowTarget()
                || this.getChatInputTarget();
            const chatRect = this.getElementRect(chatTarget);
            if (chatRect) {
                return {
                    x: chatRect.left + chatRect.width / 2,
                    y: chatRect.top + chatRect.height / 2
                };
            }

            return null;
        },

        async moveAvatarFloatingCursor(scene, primaryTarget, secondaryTarget, previousSceneId, options) {
            const normalizedOptions = options || {};
            const action = scene.cursorAction || 'move';
            const targets = [primaryTarget, secondaryTarget].filter(Boolean);
            if (action === 'tour') {
                targets.push.apply(targets, this.getAvatarFloatingCursorTourTargets(scene, primaryTarget));
            }
            const uniqueTargets = Array.from(new Set(targets));
            if (uniqueTargets.length === 0) {
                return;
            }
            const configuredFirstMoveMs = Number.isFinite(scene.cursorMoveDurationMs)
                ? Math.max(160, Math.floor(scene.cursorMoveDurationMs))
                : 0;
            if (!this.cursor.hasPosition()) {
                const origin = this.resolveAvatarFloatingCursorStartPoint(scene, uniqueTargets, previousSceneId)
                    || this.getDefaultCursorOrigin();
                this.cursor.showAt(origin.x, origin.y);
                await this.waitForSceneDelay(120);
            }
            for (let index = 0; index < uniqueTargets.length; index += 1) {
                if (this.isStopping()) {
                    return;
                }
                const moved = await this.moveCursorToElement(
                    uniqueTargets[index],
                    index === 0 ? (configuredFirstMoveMs || 760) : 520,
                    normalizedOptions
                );
                if (!moved) {
                    continue;
                }
                if (action === 'click' && index === 0) {
                    const clickPromise = this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
                    if (typeof normalizedOptions.onClickStart === 'function') {
                        normalizedOptions.onClickStart({
                            scene,
                            target: uniqueTargets[index]
                        });
                    } else if (scene && scene.operation === 'open-avatar-tool-menu') {
                        this.setChatAvatarToolMenuOpen(true, 'avatar-floating-guide-open-avatar-tool-menu');
                    }
                    await clickPromise;
                } else if (action === 'wobble' || action === 'tour') {
                    this.cursor.wobble(Number.isFinite(scene.cursorWobbleDurationMs)
                        ? Math.max(0, Math.floor(scene.cursorWobbleDurationMs))
                        : 0);
                    await this.waitForSceneDelay(action === 'tour'
                        ? 220
                        : (Number.isFinite(scene.cursorWobbleDurationMs)
                            ? Math.max(0, Math.floor(scene.cursorWobbleDurationMs))
                            : 360));
                }
            }
        },

        async moveExternalizedChatCursor(scene, options) {
            const normalizedOptions = options || {};
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            const moveWaitMs = this.getExternalizedChatCursorMoveDurationMs(scene, 760);
            await this.waitForExternalizedChatCursorMove(
                sceneId,
                moveWaitMs > 0 ? moveWaitMs : undefined
            );
            if (this.isStopping()) {
                return false;
            }
            const cursorKind = this.getExternalizedChatCursorTargetKind(scene);
            const useHomeOwnedClick = this.isDay2InteractionSceneId(sceneId);
            const externalizedClickStarted = !useHomeOwnedClick && !!(
                cursorKind
                && this.setExternalizedChatCursorEffect(cursorKind, 'click', {
                    effectDurationMs: DEFAULT_CURSOR_CLICK_VISIBLE_MS
                })
            );
            const clickPromise = useHomeOwnedClick || !externalizedClickStarted
                ? this.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS)
                : this.waitForSceneDelay(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
            if (typeof normalizedOptions.onClickStart === 'function') {
                normalizedOptions.onClickStart({
                    scene: scene,
                    kind: cursorKind
                });
            }
            await clickPromise;
            return true;
        },

        async closeAvatarFloatingGuidePanels(options) {
            const shouldClearCursor = !!(options && options.clearCursor === true);
            const preserveExternalizedChatGuideTarget = !shouldClearCursor && !!(
                options && options.preserveExternalizedChatGuideTarget === true
            );
            this.closeChatToolPopover();
            if (!preserveExternalizedChatGuideTarget) {
                this.clearExternalizedChatGuideTarget({
                    clearCursor: shouldClearCursor
                });
            }
            this.forceHideAvatarFloatingGuideManagedSurfaces();
            if (
                this.avatarFloatingGuideTemporaryHudShown
                || this.avatarFloatingGuideTemporaryHudWasVisible
            ) {
                this.hideTemporaryAvatarFloatingGuideHud('close-panels');
            }
            this.clearSceneExtraSpotlights();
            this.clearRetainedExtraSpotlights();
            this.clearSpotlightGeometryHints();
            this.clearSpotlightVariantHints();
            this.overlay.clearActionSpotlight();
            await this.closeManagedPanels().catch(() => {});
            ['agent-user-plugin', 'agent-openclaw'].forEach((toggleId) => this.collapseAgentSidePanel(toggleId));
            this.collapseCharacterSettingsSidePanel();
        },

        isDay1AvatarFloatingScene(scene) {
            return !!(
                scene
                && typeof scene.id === 'string'
                && scene.id.indexOf('day1_') === 0
            );
        },

        async playDay1IntroActivationRoundScene(sceneRunId) {
            if (!this.day1RoundWakeupCompleted) {
                await this.runWakeupPrelude();
                this.day1RoundWakeupCompleted = true;
            }
            if (this.isStopping()) {
                return false;
            }

            if (this.introFlowStarted) {
                return sceneRunId === this.sceneRunId && !this.isStopping();
            }

            this.introFlowStarted = true;
            await this.ensureGuideIdleSwayPerformance();
            this.setCurrentScene('day1_intro_activation', null);
            this.overlay.hideBubble();
            this.overlay.hidePluginPreview();

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
                this.hideHomeCursorForExternalizedChat();
                return sceneRunId === this.sceneRunId && !this.isStopping();
            }

            await this.ensureChatVisible();
            if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                return false;
            }

            const inputTarget = this.getChatInputTarget();
            const inputRect = this.getElementRect(inputTarget);
            if (inputRect) {
                const cx = inputRect.left + inputRect.width / 2;
                const cy = inputRect.top + inputRect.height / 2;
                this.cursor.showAt(cx, cy);
                this.cursor.wobble();
                const activationHint = this.resolveGuideCopy(INTRO_ACTIVATION_HINT_KEY, INTRO_ACTIVATION_HINT);
                this.showGuideBubble(activationHint, {
                    anchorRect: inputRect,
                    bubbleVariant: 'intro-activation'
                }, 'intro_activation');
                const bubbleEl = this.overlay.bubble;
                if (bubbleEl) {
                    const bubbleW = Math.min(bubbleEl.offsetWidth || 380, window.innerWidth - 32);
                    const bubbleH = bubbleEl.offsetHeight || 60;
                    const bLeft = Math.max(16, Math.min(
                        inputRect.left + inputRect.width / 2 - bubbleW / 2,
                        window.innerWidth - bubbleW - 16
                    ));
                    const bTop = Math.max(16, inputRect.top - bubbleH - 14);
                    bubbleEl.style.left = Math.round(bLeft) + 'px';
                    bubbleEl.style.top = Math.round(bTop) + 'px';
                }
                await this.waitForIntroActivationTransition();
                if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                    return false;
                }
                this.overlay.hideBubble();
                this.cursor.wobble();
                await wait(280);
            }

            return sceneRunId === this.sceneRunId && !this.isStopping();
        },

        async playDay1IntroGreetingRoundScene(sceneRunId) {
            const introStep = this.getStep('intro_basic') || {
                performance: {
                    interruptible: true
                },
                interrupts: {}
            };
            if (!this.introFlowStarted) {
                const activated = await this.playDay1IntroActivationRoundScene(sceneRunId);
                if (!activated) {
                    return false;
                }
            }
            this.setCurrentScene('day1_intro_greeting', null);
            if (!this.isHomeChatExternalized()) {
                await this.waitForSceneDelay(140);
            }
            if (sceneRunId !== this.sceneRunId || this.isStopping()) {
                return false;
            }
            this.enableInterrupts(introStep);
            if (this.isHomeChatExternalized()) {
                this.setExternalizedChatGuideTarget('capsule-input', {
                    effect: '',
                    durationMs: 0
                });
            } else {
                const inputTarget = this.getChatInputTarget();
                if (inputTarget) {
                    this.setSpotlightGeometryHint(inputTarget, {
                        padding: DEFAULT_SPOTLIGHT_PADDING + 3
                    });
                    this.overlay.setPersistentSpotlight(inputTarget);
                }
            }
            await this.playIntroGreetingReply();
            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
            }
            return sceneRunId === this.sceneRunId && !this.isStopping();
        },

        async playAvatarFloatingScene(scene, day, index, total, roundContext) {
            return this.sceneOrchestrator.playScene(scene, day, index, total, roundContext);
        },

        async playAvatarFloatingRound(round, options) {
            recordAvatarFloatingGuideRoundStart(round);
            return this.sceneOrchestrator.playRound(round, options);
        },

        recordAvatarFloatingGuideRoundEnd(round) {
            recordAvatarFloatingGuideRoundEnd(round);
        },

        disableInterrupts() {
            if (!this.interruptsEnabled) {
                return;
            }

            window.removeEventListener('mousemove', this.pointerMoveHandler, true);
            window.removeEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = false;
            this.lastPointerPoint = null;
            this.interruptQualifyingMoveStreak = 0;
        },

        enableInterrupts(step) {
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                this.disableInterrupts();
                return;
            }

            this.disableInterrupts();
            if (interrupts.resetOnStepAdvance !== false) {
                this.interruptCount = 0;
            }
            this.interruptQualifyingMoveStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            window.addEventListener('mousemove', this.pointerMoveHandler, true);
            window.addEventListener('mousedown', this.pointerDownHandler, true);
            this.interruptsEnabled = true;
        },

        playCursorResistanceToUserMotion(x, y, distance, motionDx, motionDy) {
            let hasVisibleCursor = typeof this.cursor.hasVisiblePosition === 'function'
                ? this.cursor.hasVisiblePosition()
                : this.cursor.hasPosition();
            if (!hasVisibleCursor && this.isHomeChatExternalized()) {
                const currentPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                    ? this.overlay.getCursorPosition()
                    : null;
                if (
                    currentPoint
                    && Number.isFinite(currentPoint.x)
                    && Number.isFinite(currentPoint.y)
                    && typeof this.cursor.showAt === 'function'
                ) {
                    this.cursor.showAt(currentPoint.x, currentPoint.y);
                    hasVisibleCursor = true;
                } else if (
                    typeof this.restoreCursorFromExternalizedChatAnchor === 'function'
                    && this.restoreCursorFromExternalizedChatAnchor(30000)
                ) {
                    hasVisibleCursor = true;
                }
            }
            if (!hasVisibleCursor) {
                return;
            }

            if (!Number.isFinite(distance) || distance <= 0) {
                return;
            }

            this.cursor.reactToUserMotion(x, y, {
                motionDx: motionDx,
                motionDy: motionDy,
                scale: 0.4,
                outDurationMs: 140,
                backDurationMs: 240,
                forcePcOverlay: true
            });
        },

        isCursorTransientMotionActive() {
            return !!(
                this.cursor
                && typeof this.cursor.isTransientMotionActive === 'function'
                && this.cursor.isTransientMotionActive()
            );
        },

        async waitForCursorTransientMotion() {
            if (
                this.cursor
                && typeof this.cursor.waitForTransientMotion === 'function'
                && this.isCursorTransientMotionActive()
            ) {
                await this.cursor.waitForTransientMotion();
            }
        },

        shouldAllowInterruptDuringCurrentScene() {
            if (!this.interruptsEnabled || this.destroyed || this.angryExitTriggered) {
                return false;
            }

            if (
                this.page === 'home'
                && this.pluginDashboardHandoff
                && this.pluginDashboardHandoff.windowRef
                && !this.pluginDashboardHandoff.windowRef.closed
            ) {
                return false;
            }

            if (this.page !== 'home') {
                return true;
            }

            if (this.currentSceneId === 'intro_basic') {
                return this.introFlowStarted && !this.isStopping();
            }

            return !!this.currentSceneId;
        },

        // Dev B boundary: Director only talks to this API surface.
        // Dev C can later provide a real implementation via options.homeInteractionApi,
        // window.getYuiGuideHomeInteractionApi(), window.YuiGuideHomeInteractionApi,
        // or the broader window.YuiGuidePageHandoff module.
    });
})(window.__YuiGuideDirector);
