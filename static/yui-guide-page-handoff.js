/**
 * Yui Guide Page Handoff — 统一页面打开 API
 *
 * Dev C 专属模块（首页交互与跨页负责人）。
 * M1 阶段只提供统一页面打开包装；M3 阶段扩展跨页 handoff 与 scene 恢复。
 *
 * 锚点验证结果（M1 基线，2026-04-15）:
 * ┌──────────────────────────┬───────────────────────────────────────┬────────┐
 * │ 场景 ID                  │ 锚点选择器                            │ 状态   │
 * ├──────────────────────────┼───────────────────────────────────────┼────────┤
 * │ intro_basic              │ #text-input-area                      │ OK *   │
 * │ intro_proactive          │ #${p}-toggle-proactive-chat           │ OK     │
 * │ intro_cat_paw            │ #${p}-btn-agent                       │ OK     │
 * │ takeover_capture_cursor  │ #${p}-btn-agent                       │ OK     │
 * │ takeover_plugin_preview  │ #${p}-btn-agent                       │ OK     │
 * │ takeover_settings_peek   │ #${p}-btn-settings                    │ OK     │
 * │ takeover_return_control  │ #${p}-container                       │ OK     │
 * │ interrupt_resist_light   │ #${p}-container                       │ OK     │
 * │ interrupt_angry_exit     │ #${p}-container                       │ OK     │
 * │ handoff_api_key          │ #${p}-menu-api-keys                   │ OK **  │
 * │ handoff_memory_browser   │ #${p}-menu-memory                     │ OK **  │
 * │ handoff_steam_workshop   │ #${p}-menu-steam-workshop             │ OK **  │
 * │ handoff_plugin_dashboard │ #${p}-btn-agent                       │ OK     │
 * └──────────────────────────┴───────────────────────────────────────┴────────┘
 *  * #text-input-area 在 #chat-container(display:none!important) 内，
 *    仅由 startPrelude() 使用，不作为 driver.js 高亮目标，可接受。
 * ** 由 Dev C M1 在 avatar-ui-popup.js _createMenuItem() 中补设 DOM ID。
 *
 * ${p} 占位符由主负责人的 Director 在运行时解析为实际模型前缀（live2d/vrm/mmd）。
 */
(function () {
    'use strict';

    var WINDOW_NAME_PREFIX = 'neko_';
    var WINDOW_CHECK_INTERVAL_MS = 1000;
    var DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME = 'ATLS';

    var _activeWindows = {};
    var _activeTimers = {};
    var _pluginDashboardCompleteDeprecatedWarned = false;

    function syncMainUIVisibility() {
        var hasOpenWindow = Object.keys(_activeWindows).some(function (key) {
            var win = _activeWindows[key];
            return !!(win && !win.closed);
        });

        if (hasOpenWindow) {
            return;
        }

        if (typeof window.handleShowMainUI === 'function') {
            try {
                window.handleShowMainUI();
            } catch (_) {}
        }
    }

    /**
     * 规范化窗口名称：简写自动补 neko_ 前缀。
     * 'api_key' -> 'neko_api_key'
     * 'neko_api_key' -> 'neko_api_key'
     */
    function normalizeWindowName(name) {
        if (!name) return '';
        if (name.indexOf(WINDOW_NAME_PREFIX) === 0) return name;
        return WINDOW_NAME_PREFIX + name;
    }

    /**
     * 打开目标页面并暂停主页渲染。
     *
     * @param {string} openUrl - 目标页面路径，如 '/api_key'
     * @param {string} windowName - 窗口名称简写，如 'api_key'，内部自动补前缀
     * @param {string} [features] - 可选的 window.open features 字符串
     * @returns {Promise<Window|null>} 子窗口引用，失败时返回 null
     */
    function openPage(openUrl, windowName, features, options) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) {
            console.warn('[YuiGuideHandoff] windowName 为空，取消打开');
            return Promise.resolve(null);
        }
        var normalizedOptions = options || {};
        var targetUrl = openUrl;
        try {
            targetUrl = new URL(openUrl, window.location.origin).toString();
        } catch (_) {}
        var existingWindow = getOpenedWindow(fullName);
        var hadExistingWindow = !!(existingWindow && !existingWindow.closed);
        var childWin;

        if (typeof window.openOrFocusWindow === 'function') {
            childWin = window.openOrFocusWindow(targetUrl, fullName, features);
        } else {
            childWin = window.open(targetUrl, fullName, features);
        }

        if (!childWin) {
            console.warn('[YuiGuideHandoff] 窗口打开失败或被拦截:', targetUrl);
            return Promise.resolve(null);
        }

        _activeWindows[fullName] = childWin;
        if (!window._openedWindows) {
            window._openedWindows = {};
        }
        window._openedWindows[fullName] = childWin;

        try {
            childWin.focus();
        } catch (_) {}

        if (normalizedOptions.forceReload && hadExistingWindow) {
            try {
                childWin.location.replace(targetUrl);
            } catch (_) {
                try {
                    childWin.location.href = targetUrl;
                } catch (error) {
                    console.warn('[YuiGuideHandoff] 窗口重载失败:', targetUrl, error);
                }
            }
        }

        if (!normalizedOptions.keepMainUIVisible && typeof window.handleHideMainUI === 'function') {
            window.handleHideMainUI();
        }

        return Promise.resolve(childWin);
    }

    /**
     * 检查指定窗口是否仍然打开。
     *
     * @param {string} windowName - 窗口名称简写
     * @returns {boolean}
     */
    function isWindowOpen(windowName) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) return false;
        var win = _activeWindows[fullName];
        if (!win) return false;
        if (win.closed) {
            delete _activeWindows[fullName];
            syncMainUIVisibility();
            return false;
        }
        return true;
    }

    /**
     * 当目标窗口关闭时执行回调（轮询检测）。
     *
     * @param {string} windowName - 窗口名称简写
     * @param {Function} onReturn - 窗口关闭后的回调
     * @returns {void}
     */
    function resumeOnReturn(windowName, onReturn) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) {
            if (typeof onReturn === 'function') onReturn();
            return;
        }

        if (_activeTimers[fullName]) return;

        var win = _activeWindows[fullName];

        if (!win || win.closed) {
            delete _activeWindows[fullName];
            syncMainUIVisibility();
            if (typeof onReturn === 'function') onReturn();
            return;
        }

        _activeTimers[fullName] = true;
        var timer = setInterval(function () {
            if (win.closed) {
                clearInterval(timer);
                if (_activeWindows[fullName] === win) {
                    delete _activeWindows[fullName];
                }
                delete _activeTimers[fullName];
                syncMainUIVisibility();
                if (typeof onReturn === 'function') onReturn();
            }
        }, WINDOW_CHECK_INTERVAL_MS);
    }

    // ─── 内部：弹层工具 ──────────────────────────────────────

    var POPUP_OPEN_ANIMATION_MS = 250;
    var HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token';
    var HANDOFF_CONSUMED_NOTIFY_KEY = 'neko_yui_guide_handoff_consumed';
    var HANDOFF_TOKEN_VERSION = 1;
    var HANDOFF_TOKEN_TTL_MS = 5 * 60 * 1000;
    var HANDOFF_FLOW_ID = 'home_yui_guide_v1';
    var DEFAULT_PLUGIN_DASHBOARD_ORIGIN = 'http://127.0.0.1:48916';
    var PLUGIN_DASHBOARD_DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done';
    var PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request';
    var PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack';
    var _processedPluginDashboardCompletionTokens = {};

    function generateTokenId() {
        return 'h_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 10);
    }

    var HANDOFF_SESSION_ID = generateTokenId();

    function getPrefix() {
        if (typeof window.UniversalTutorialManager === 'function' &&
            typeof window.UniversalTutorialManager.detectModelPrefix === 'function') {
            return window.UniversalTutorialManager.detectModelPrefix();
        }
        if (window.lanlan_config && window.lanlan_config.model_type) {
            var mt = window.lanlan_config.model_type;
            if (mt === 'vrm' || mt === 'mmd') return mt;
            if (mt === 'live3d') {
                if (window.mmdManager && window.mmdManager.currentModel) return 'mmd';
                if (window.vrmManager && window.vrmManager.currentModel) return 'vrm';
            }
        }
        return 'live2d';
    }

    function getManager(prefix) {
        var p = prefix || getPrefix();
        return window[p + 'Manager'] || null;
    }

    function getPopup(buttonId, prefix) {
        var p = prefix || getPrefix();
        return document.getElementById(p + '-popup-' + buttonId);
    }

    function getFloatingButton(buttonId, prefix) {
        var p = prefix || getPrefix();
        return document.getElementById(p + '-btn-' + buttonId);
    }

    function getPluginDashboardExpectedOrigin() {
        if (window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN) {
            try {
                return new URL(String(window.YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN), window.location.href).origin;
            } catch (e) {
                console.warn('[YuiGuideHandoff] getPluginDashboardExpectedOrigin: YUI_GUIDE_PLUGIN_DASHBOARD_ORIGIN 无效:', e);
            }
        }

        if (window.NEKO_USER_PLUGIN_BASE) {
            try {
                return new URL(String(window.NEKO_USER_PLUGIN_BASE), window.location.href).origin;
            } catch (e) {
                console.warn('[YuiGuideHandoff] getPluginDashboardExpectedOrigin: NEKO_USER_PLUGIN_BASE 无效:', e);
            }
        }

        return DEFAULT_PLUGIN_DASHBOARD_ORIGIN;
    }

    function buildPluginDashboardUrl(params) {
        var origin = getPluginDashboardExpectedOrigin();
        var normalizedOrigin = DEFAULT_PLUGIN_DASHBOARD_ORIGIN;

        if (origin) {
            try {
                normalizedOrigin = new URL(String(origin)).origin;
            } catch (e) {
                console.warn('[YuiGuideHandoff] buildPluginDashboardUrl: dashboard origin 无效，回退默认值:', e);
            }
        }

        var url = new URL('/ui/', normalizedOrigin);
        (Array.isArray(params) ? params : []).forEach(function (entry) {
            if (typeof entry !== 'string' || !entry) return;
            var equalIndex = entry.indexOf('=');
            if (equalIndex < 0) {
                url.searchParams.append(entry, '');
                return;
            }

            var key = entry.slice(0, equalIndex);
            var value = entry.slice(equalIndex + 1);
            try {
                url.searchParams.append(key, decodeURIComponent(value));
            } catch (_) {
                url.searchParams.append(key, value);
            }
        });

        return url.toString();
    }

    function getHandoffTokenSignature(tokenObj) {
        if (!tokenObj) return '';
        return tokenObj.signature || tokenObj.id || tokenObj.token || '';
    }

    function dispatchHandoffConsumedEvent(detail) {
        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-consumed', {
            detail: detail || {}
        }));
    }

    function notifyHandoffConsumed(detail) {
        var payload = detail || {};
        dispatchHandoffConsumedEvent(payload);
        try {
            localStorage.setItem(HANDOFF_CONSUMED_NOTIFY_KEY, JSON.stringify({
                detail: payload,
                emitted_at: Date.now(),
                sessionId: HANDOFF_SESSION_ID
            }));
        } catch (error) {
            console.warn('[YuiGuideHandoff] notifyHandoffConsumed: 广播失败:', error);
        }
    }

    function createHandoffToken(targetPage, resumeScene) {
        var now = Date.now();
        var tokenObj = {
            token: 'h_' + now.toString(36) + '_' + Math.random().toString(36).substring(2, 10),
            token_version: HANDOFF_TOKEN_VERSION,
            flow_id: HANDOFF_FLOW_ID,
            source_page: 'home',
            target_page: targetPage || '',
            resume_scene: resumeScene || null,
            created_at: now,
            expires_at: now + HANDOFF_TOKEN_TTL_MS
        };

        try {
            localStorage.setItem(HANDOFF_STORAGE_KEY, JSON.stringify(tokenObj));
        } catch (error) {
            console.error('[YuiGuideHandoff] createHandoffToken: 存储失败:', error);
            return null;
        }

        return tokenObj;
    }

    function clearHandoffToken() {
        try {
            localStorage.removeItem(HANDOFF_STORAGE_KEY);
        } catch (_) {}
    }

    function readHandoffToken() {
        try {
            var raw = localStorage.getItem(HANDOFF_STORAGE_KEY);
            if (!raw) return null;

            var tokenObj = JSON.parse(raw);
            if (!tokenObj || !tokenObj.token || tokenObj.token_version !== HANDOFF_TOKEN_VERSION) {
                return null;
            }

            if (Date.now() > tokenObj.expires_at) {
                clearHandoffToken();
                return null;
            }

            return tokenObj;
        } catch (error) {
            console.error('[YuiGuideHandoff] readHandoffToken: 读取失败:', error);
            return null;
        }
    }

    function consumeHandoffToken(expectedPage) {
        var tokenObj = readHandoffToken();
        if (!tokenObj) return null;

        if (expectedPage && tokenObj.target_page !== expectedPage) {
            console.warn('[YuiGuideHandoff] consumeHandoffToken: 页面不匹配, 期望:', expectedPage, '实际:', tokenObj.target_page);
            return null;
        }

        if (tokenObj.consumed) {
            return null;
        }

        var expectedSignature = getHandoffTokenSignature(tokenObj);
        if (!expectedSignature) {
            console.warn('[YuiGuideHandoff] consumeHandoffToken: token 缺少稳定标识');
            return null;
        }

        var currentTokenObj = readHandoffToken();
        if (!currentTokenObj || currentTokenObj.consumed) {
            return null;
        }

        if (getHandoffTokenSignature(currentTokenObj) !== expectedSignature) {
            console.warn('[YuiGuideHandoff] consumeHandoffToken: token 已变化，取消消费');
            return null;
        }

        var consumedTokenObj = Object.assign({}, currentTokenObj, {
            consumed: true,
            consumed_by: HANDOFF_SESSION_ID,
            consumed_at: Date.now()
        });

        try {
            localStorage.setItem(HANDOFF_STORAGE_KEY, JSON.stringify(consumedTokenObj));
        } catch (error) {
            console.error('[YuiGuideHandoff] consumeHandoffToken: 标记消费失败:', error);
            return null;
        }

        var storedTokenObj = readHandoffToken();
        if (!storedTokenObj || !storedTokenObj.consumed) {
            return null;
        }
        if (getHandoffTokenSignature(storedTokenObj) !== expectedSignature) {
            return null;
        }
        if (storedTokenObj.consumed_by !== HANDOFF_SESSION_ID) {
            return null;
        }

        notifyHandoffConsumed({
            token: storedTokenObj.token,
            target_page: storedTokenObj.target_page || '',
            resume_scene: storedTokenObj.resume_scene || null,
            consumed_by: storedTokenObj.consumed_by,
            consumed_at: storedTokenObj.consumed_at,
            source_page: storedTokenObj.source_page || '',
            flow_id: storedTokenObj.flow_id || '',
            expected_page: expectedPage || null
        });

        return storedTokenObj;
    }

    function waitFor(condition, timeoutMs, intervalMs) {
        var timeout = Number.isFinite(timeoutMs) ? timeoutMs : 4000;
        var interval = Number.isFinite(intervalMs) ? intervalMs : 80;
        var startedAt = Date.now();

        return new Promise(function (resolve) {
            function tick() {
                var result = null;
                try {
                    result = condition();
                } catch (_) {
                    result = null;
                }

                if (result) {
                    resolve(result);
                    return;
                }

                if ((Date.now() - startedAt) >= timeout) {
                    resolve(null);
                    return;
                }

                setTimeout(tick, interval);
            }

            tick();
        });
    }

    function getAgentToggleElement(toggleId) {
        var prefix = getPrefix();
        return document.getElementById(prefix + '-toggle-' + toggleId);
    }

    function getAgentToggleCheckbox(toggleId) {
        var prefix = getPrefix();
        return document.getElementById(prefix + '-' + toggleId);
    }

    function getAgentSidePanel(toggleId) {
        return document.querySelector('[data-neko-sidepanel-type="' + toggleId + '-actions"]');
    }

    function isSidePanelVisible(sidePanel) {
        return !!(sidePanel && sidePanel.style.display === 'flex' && sidePanel.style.opacity !== '0');
    }

    function getAgentSidePanelAction(toggleId, actionId) {
        if (!toggleId || !actionId) return null;
        return document.getElementById('neko-sidepanel-action-' + toggleId + '-' + actionId);
    }

    function getOpenedWindow(fullName) {
        if (!fullName) return null;
        var tracked = _activeWindows[fullName];
        if (tracked && !tracked.closed) {
            return tracked;
        }

        if (window._openedWindows && window._openedWindows[fullName] && !window._openedWindows[fullName].closed) {
            return window._openedWindows[fullName];
        }

        return null;
    }

    function sendAgentCommand(command, payload) {
        var requestId = Date.now() + '-' + Math.random().toString(36).slice(2, 8);
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timeoutId = window.setTimeout(function () {
            if (controller) {
                controller.abort();
            }
        }, 4000);
        return fetch('/api/agent/command', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            signal: controller ? controller.signal : undefined,
            body: JSON.stringify(Object.assign({
                request_id: requestId,
                command: command
            }, payload || {}))
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('command status ' + response.status);
            }
            return response.json();
        }).then(function (data) {
            if (!data || data.success !== true) {
                throw new Error((data && data.error) || 'command failed');
            }
            return data;
        }).catch(function (error) {
            console.warn('[YuiGuideHandoff] sendAgentCommand 失败:', command, error);
            return {
                success: false,
                error: error && error.message ? error.message : 'command failed'
            };
        }).finally(function () {
            window.clearTimeout(timeoutId);
        });
    }

    function syncAgentToggleDom(toggleId, checked) {
        var checkbox = getAgentToggleCheckbox(toggleId);
        var toggleItem = getAgentToggleElement(toggleId);
        if (!checkbox || !toggleItem) {
            return;
        }

        checkbox.checked = !!checked;
        toggleItem.setAttribute('aria-checked', checked ? 'true' : 'false');
        if (typeof checkbox._updateStyle === 'function') {
            checkbox._updateStyle();
        }
    }

    function dispatchSyntheticPress(element) {
        if (!element) {
            return;
        }

        try {
            element.dispatchEvent(new MouseEvent('mouseenter', {
                bubbles: true,
                cancelable: true,
                view: window
            }));
            element.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true,
                cancelable: true,
                view: window
            }));
            element.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true,
                cancelable: true,
                view: window
            }));
        } catch (_) {}
    }

    function buildCenteredWindowFeatures(width, height) {
        var w = Number.isFinite(width) ? width : Math.min(1280, Math.round(screen.width * 0.8));
        var h = Number.isFinite(height) ? height : Math.min(900, Math.round(screen.height * 0.8));
        var left = Math.max(0, Math.floor((screen.width - w) / 2));
        var top = Math.max(0, Math.floor((screen.height - h) / 2));
        return 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top + ',menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes';
    }

    function getTutorialModelManagerLanlanName() {
        var explicitName = typeof window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME === 'string'
            ? window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME.trim()
            : '';
        if (explicitName) {
            return explicitName;
        }

        return DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME;
    }

    function getModelManagerWindowName(name, prefix) {
        var normalizedName = typeof name === 'string' && name.trim()
            ? name.trim()
            : getTutorialModelManagerLanlanName();
        var modelPrefix = prefix || getPrefix();
        var stem = modelPrefix === 'vrm'
            ? 'vrm-manage_'
            : modelPrefix === 'mmd'
            ? 'mmd-manage_'
            : 'live2d-manage_';
        return stem + encodeURIComponent(normalizedName);
    }

    // ─── M2: 首页交互包装 API ────────────────────────────────

    /**
     * 打开设置弹层。
     * 教程引导调用后，设置菜单项（#${p}-menu-* / #${p}-toggle-*）变为可定位。
     *
     * @returns {Promise<boolean>} 弹层是否成功打开
     */
    function openSettingsPanel() {
        var prefix = getPrefix();
        var manager = getManager(prefix);
        var popup = getPopup('settings', prefix);
        var button = getFloatingButton('settings', prefix);

        if (!manager || !popup || typeof manager.showPopup !== 'function') {
            console.warn('[YuiGuideHandoff] openSettingsPanel: manager/showPopup 不可用');
            return Promise.resolve(false);
        }

        if (popup.style.display === 'flex') {
            return Promise.resolve(true);
        }

        dispatchSyntheticPress(button);
        manager.showPopup('settings', popup);

        // 不用固定 setTimeout（低端机/教程首启动主线程繁忙时，
        // 250ms 内 reflow + style 应用未必完成，会被误判为打开失败）。
        // 改用轮询 waitFor，跟 closeSettingsPanel 保持对称。
        return waitFor(function () {
            return popup.style.display === 'flex' ? true : null;
        }, POPUP_OPEN_ANIMATION_MS + 500).then(function (opened) {
            return !!opened;
        });
    }

    /**
     * 关闭设置弹层。
     *
     * @returns {Promise<boolean>} 弹层是否成功关闭
     */
    function closeSettingsPanel() {
        var manager = getManager();
        if (!manager || typeof manager.closePopupById !== 'function') {
            return Promise.resolve(false);
        }
        var popup = getPopup('settings');
        if (!popup || popup.style.display !== 'flex') {
            return Promise.resolve(true);
        }

        manager.closePopupById('settings');

        return waitFor(function () {
            return !popup || popup.style.display !== 'flex' ? true : null;
        }, POPUP_OPEN_ANIMATION_MS + 500).then(function (closed) {
            return !!closed;
        });
    }

    /**
     * 打开 Agent / 猫爪弹层。
     * 用于 takeover_plugin_preview 等需要展示真实 Agent 能力面板的场景。
     *
     * @returns {Promise<boolean>} 弹层是否成功打开
     */
    function openAgentPanel() {
        var prefix = getPrefix();
        var manager = getManager(prefix);
        var popup = getPopup('agent', prefix);
        var button = getFloatingButton('agent', prefix);

        if (!manager || !popup || typeof manager.showPopup !== 'function') {
            console.warn('[YuiGuideHandoff] openAgentPanel: manager/showPopup 不可用');
            return Promise.resolve(false);
        }

        if (popup.style.display === 'flex') {
            return Promise.resolve(true);
        }

        dispatchSyntheticPress(button);
        manager.showPopup('agent', popup);

        // 同 openSettingsPanel：用轮询 waitFor 替代固定 250ms，
        // 避免低端机首启动时被误判为打开失败导致下游链路短路。
        return waitFor(function () {
            return popup.style.display === 'flex' ? true : null;
        }, POPUP_OPEN_ANIMATION_MS + 500).then(function (opened) {
            return !!opened;
        });
    }

    function closeAgentPanel() {
        var manager = getManager();
        document.querySelectorAll('[data-neko-sidepanel-type="agent-user-plugin-actions"], [data-neko-sidepanel-type="agent-openclaw-actions"]').forEach(function (panel) {
            if (!panel) return;
            if (panel._hoverCollapseTimer) {
                clearTimeout(panel._hoverCollapseTimer);
                panel._hoverCollapseTimer = null;
            }
            if (panel._collapseTimeout) {
                clearTimeout(panel._collapseTimeout);
                panel._collapseTimeout = null;
            }
            if (typeof panel._collapse === 'function') {
                panel._collapse();
                return;
            }
            panel.style.transition = 'none';
            panel.style.opacity = '0';
            panel.style.display = 'none';
            panel.style.pointerEvents = 'none';
            panel.style.transition = '';
        });
        if (!manager || typeof manager.closePopupById !== 'function') {
            return Promise.resolve(false);
        }
        var popup = getPopup('agent');
        if (!popup || popup.style.display !== 'flex') {
            return Promise.resolve(true);
        }

        manager.closePopupById('agent');

        return waitFor(function () {
            return !popup || popup.style.display !== 'flex' ? true : null;
        }, POPUP_OPEN_ANIMATION_MS + 500).then(function (closed) {
            return !!closed;
        });
    }

    /**
     * 确保设置弹层已打开且指定菜单项可见、可被教程高亮定位。
     *
     * 如果弹层未打开，会先打开它；然后滚动/展开使目标菜单项进入视口。
     *
     * @param {string} menuId - 菜单项 DOM ID 后缀，如 'api-keys'、'memory'、'character'
     *                           自动拼接为 #${prefix}-menu-${menuId}
     * @returns {Promise<boolean>} 菜单项是否可见
     */
    function ensureSettingsMenuVisible(menuId) {
        if (!menuId) return Promise.resolve(false);
        var prefix = getPrefix();

        return openSettingsPanel().then(function (opened) {
            if (!opened) return false;

            var el = document.getElementById(prefix + '-menu-' + menuId);
            if (!el) {
                console.warn('[YuiGuideHandoff] ensureSettingsMenuVisible: 菜单项不存在:', menuId);
                return false;
            }

            if (typeof el.scrollIntoView === 'function') {
                el.scrollIntoView({ block: 'nearest', behavior: 'instant' });
            }

            return true;
        });
    }

    function ensureAgentToggleChecked(toggleId, checked) {
        var desiredChecked = checked !== false;
        if (!toggleId) return Promise.resolve(false);

        return openAgentPanel().then(function (opened) {
            if (!opened) return false;

            return waitFor(function () {
                var checkbox = getAgentToggleCheckbox(toggleId);
                var toggleItem = getAgentToggleElement(toggleId);
                if (!checkbox || !toggleItem) {
                    return null;
                }

                if (checkbox.disabled) {
                    return null;
                }

                return {
                    checkbox: checkbox,
                    toggleItem: toggleItem
                };
            }, 5000).then(function (parts) {
                if (!parts || !parts.checkbox || !parts.toggleItem) {
                    console.warn('[YuiGuideHandoff] ensureAgentToggleChecked: toggle 不可用:', toggleId);
                    return false;
                }

                if (!!parts.checkbox.checked === desiredChecked) {
                    return true;
                }

                parts.toggleItem.click();
                return waitFor(function () {
                    return !!parts.checkbox.checked === desiredChecked ? true : null;
                }, 1500).then(function (result) {
                    return !!result;
                });
            });
        });
    }

    function setAgentMasterEnabled(enabled) {
        return sendAgentCommand('set_agent_enabled', {
            enabled: !!enabled
        }).then(function (result) {
            if (!result || result.success !== true) {
                return false;
            }
            dispatchSyntheticPress(getAgentToggleElement('agent-master'));
            syncAgentToggleDom('agent-master', !!enabled);
            if (!enabled) {
                syncAgentToggleDom('agent-user-plugin', false);
            }
            return true;
        }).catch(function (error) {
            console.warn('[YuiGuideHandoff] setAgentMasterEnabled 失败:', error);
            return false;
        });
    }

    function setAgentFlagEnabled(flagKey, enabled) {
        var key = typeof flagKey === 'string' ? flagKey.trim() : '';
        if (!key) {
            return Promise.resolve(false);
        }

        var toggleMap = {
            user_plugin_enabled: 'agent-user-plugin',
            computer_use_enabled: 'agent-keyboard',
            browser_use_enabled: 'agent-browser',
            openclaw_enabled: 'agent-openclaw',
            openfang_enabled: 'agent-openfang'
        };

        return sendAgentCommand('set_flag', {
            key: key,
            value: !!enabled
        }).then(function (result) {
            if (!result || result.success !== true) {
                return false;
            }
            if (toggleMap[key]) {
                dispatchSyntheticPress(getAgentToggleElement(toggleMap[key]));
                syncAgentToggleDom(toggleMap[key], !!enabled);
            }
            return true;
        }).catch(function (error) {
            console.warn('[YuiGuideHandoff] setAgentFlagEnabled 失败:', key, error);
            return false;
        });
    }

    function ensureAgentSidePanelVisible(toggleId) {
        if (!toggleId) return Promise.resolve(false);

        return openAgentPanel().then(function (opened) {
            if (!opened) return false;

            var toggleItem = getAgentToggleElement(toggleId);
            var sidePanel = getAgentSidePanel(toggleId);
            if (!toggleItem || !sidePanel) {
                console.warn('[YuiGuideHandoff] ensureAgentSidePanelVisible: side panel 不存在:', toggleId);
                return false;
            }

            if (typeof sidePanel._expand === 'function') {
                if (sidePanel._hoverCollapseTimer) {
                    clearTimeout(sidePanel._hoverCollapseTimer);
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

            return waitFor(function () {
                return isSidePanelVisible(sidePanel) ? sidePanel : null;
            }, 1500).then(function (panel) {
                return !!panel;
            });
        });
    }

    function ensureAgentSidePanelActionVisible(toggleId, actionId, timeoutMs) {
        if (!toggleId || !actionId) return Promise.resolve(null);
        var normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 1800;

        return ensureAgentSidePanelVisible(toggleId).then(function (visible) {
            if (!visible) return null;

            return waitFor(function () {
                var sidePanel = getAgentSidePanel(toggleId);
                var button = getAgentSidePanelAction(toggleId, actionId);
                if (!sidePanel || !button || !isSidePanelVisible(sidePanel)) {
                    return null;
                }
                return button.offsetParent !== null ? button : null;
            }, normalizedTimeoutMs).then(function (button) {
                return button || null;
            });
        });
    }

    function clickAgentSidePanelAction(toggleId, actionId, options) {
        if (!toggleId || !actionId) return Promise.resolve(false);

        return ensureAgentSidePanelActionVisible(toggleId, actionId).then(function (button) {
            if (!button || typeof button.click !== 'function') {
                console.warn('[YuiGuideHandoff] clickAgentSidePanelAction: action 不存在:', toggleId, actionId);
                return false;
            }

            if (toggleId === 'agent-user-plugin' && actionId === 'management-panel') {
                // openPluginDashboard 内部 openPage 在 resolve 前已经把 childWin
                // 同步写入 _activeWindows，再走 waitForWindowOpen 只是冗余 round-trip
                return openPluginDashboard(options).then(function (childWin) {
                    return !!childWin;
                });
            }

            button.click();
            return true;
        });
    }

    function waitForWindowOpen(windowName, timeoutMs) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) return Promise.resolve(null);

        return waitFor(function () {
            return getOpenedWindow(fullName);
        }, timeoutMs || 6000, 120);
    }

    function closeWindow(windowName) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) return Promise.resolve(false);

        var target = getOpenedWindow(fullName);
        if (!target) {
            delete _activeWindows[fullName];
            if (window._openedWindows) {
                delete window._openedWindows[fullName];
            }
            syncMainUIVisibility();
            return Promise.resolve(true);
        }

        try {
            target.close();
        } catch (error) {
            console.warn('[YuiGuideHandoff] closeWindow 失败:', fullName, error);
            return Promise.resolve(false);
        }

        delete _activeWindows[fullName];
        if (window._openedWindows) {
            delete window._openedWindows[fullName];
        }
        syncMainUIVisibility();
        return Promise.resolve(true);
    }

    function openPluginDashboard(resumeSceneOrOptions, options) {
        var firstArgIsString = typeof resumeSceneOrOptions === 'string';
        var legacyResumeScene = firstArgIsString ? resumeSceneOrOptions : '';
        var sourceOptions;
        if (firstArgIsString) {
            sourceOptions = (options && typeof options === 'object') ? options : {};
        } else if (resumeSceneOrOptions && typeof resumeSceneOrOptions === 'object') {
            sourceOptions = resumeSceneOrOptions;
        } else {
            sourceOptions = {};
        }
        var normalizedOptions = Object.assign({}, sourceOptions, {
            resumeScene: legacyResumeScene || sourceOptions.resumeScene || sourceOptions.resume_scene || null,
            forceReload: true
        });
        return openPage(
            buildPluginDashboardUrl(),
            'plugin_dashboard',
            buildCenteredWindowFeatures(),
            normalizedOptions
        );
    }

    function dispatchHandoffSentEvent(tokenObj, targetPage, resumeScene) {
        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-sent', {
            detail: {
                token: tokenObj && tokenObj.token ? tokenObj.token : '',
                target_page: targetPage || '',
                resume_scene: resumeScene || null
            }
        }));
    }

    /**
     * @deprecated Plugin dashboard guide handoff now uses
     * neko:yui-guide:plugin-dashboard:start and
     * neko:yui-guide:plugin-dashboard:interrupt-ack messages.
     */
    function listenPluginDashboardComplete(onComplete, onInterrupt) {
        var callbacks = typeof onComplete === 'object' && onComplete
            ? onComplete
            : {
                onComplete: onComplete,
                onInterrupt: onInterrupt
            };
        var completeCallback = typeof callbacks.onComplete === 'function' ? callbacks.onComplete : null;
        var interruptCallback = typeof callbacks.onInterrupt === 'function' ? callbacks.onInterrupt : null;
        if (!completeCallback && !interruptCallback) return function () {};
        if (!_pluginDashboardCompleteDeprecatedWarned) {
            _pluginDashboardCompleteDeprecatedWarned = true;
            console.warn('[YuiGuideHandoff] listenPluginDashboardComplete 已弃用，请改用 neko:yui-guide:plugin-dashboard:start / neko:yui-guide:plugin-dashboard:interrupt-ack 消息流程。');
        }

        function pruneProcessedMessages() {
            Object.keys(_processedPluginDashboardCompletionTokens).forEach(function (processedToken) {
                if ((Date.now() - _processedPluginDashboardCompletionTokens[processedToken]) > HANDOFF_TOKEN_TTL_MS) {
                    delete _processedPluginDashboardCompletionTokens[processedToken];
                }
            });
        }

        function markProcessed(messageKey) {
            if (!messageKey) return false;
            pruneProcessedMessages();
            if (_processedPluginDashboardCompletionTokens[messageKey]) {
                return false;
            }
            _processedPluginDashboardCompletionTokens[messageKey] = Date.now();
            return true;
        }

        function isTrustedPluginDashboardMessage(event) {
            var expectedOrigin = getPluginDashboardExpectedOrigin();
            if (!expectedOrigin || event.origin !== expectedOrigin) {
                return false;
            }

            var fullName = normalizeWindowName('plugin_dashboard');
            var trackedWindow = (_activeWindows && _activeWindows[fullName])
                || (window._openedWindows && window._openedWindows[fullName])
                || null;
            if (trackedWindow) {
                if (trackedWindow.closed) {
                    return false;
                }
                return event.source === trackedWindow;
            }

            if (!window.opener || window.opener.closed) {
                return false;
            }
            return event.source === window.opener;
        }

        function handleTokenBackedComplete(data, detail) {
            if (data.type !== 'neko:yui-guide:plugin-dashboard-complete') return false;
            var flowId = detail.flow_id || detail.flow || '';
            var tokenId = detail.handoff_token || detail.token || '';
            var tokenObj = readHandoffToken();
            if (!tokenObj || !tokenId) return true;
            if (flowId !== (tokenObj.flow_id || HANDOFF_FLOW_ID)) return true;
            if (tokenId !== tokenObj.token) return true;
            if (!markProcessed('legacy:' + tokenId)) return true;

            if (completeCallback) {
                completeCallback(detail);
            }
            return true;
        }

        function handleTokenlessPluginDashboardMessage(data, detail) {
            var tokenObj = readHandoffToken();
            var hasActivePluginDashboardToken = !!(
                tokenObj
                && !tokenObj.consumed
                && tokenObj.target_page === 'plugin_dashboard'
            );
            var isTokenHandoffMessage = !!(
                data.type === 'neko:yui-guide:plugin-dashboard-complete'
                || detail.handoff_token
                || detail.token
            );
            if (hasActivePluginDashboardToken && isTokenHandoffMessage) return false;

            var sessionId = typeof data.sessionId === 'string' ? data.sessionId : '';
            var requestId = typeof data.requestId === 'string' ? data.requestId : '';
            if (!sessionId) return false;
            if (data.type === PLUGIN_DASHBOARD_DONE_EVENT) {
                if (!markProcessed('done:' + sessionId)) return true;
                if (completeCallback) {
                    completeCallback(Object.assign({}, detail, {
                        sessionId: sessionId
                    }));
                }
                return true;
            }
            if (data.type === PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT || data.type === PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT) {
                if (!interruptCallback) return true;
                if (!requestId) return true;
                if (!markProcessed('interrupt:' + data.type + ':' + requestId)) return true;
                interruptCallback(Object.assign({}, detail, {
                    sessionId: sessionId,
                    requestId: requestId,
                    type: data.type
                }));
                return true;
            }
            return false;
        }

        function handler(event) {
            if (!event.data || typeof event.data !== 'object') return;
            if (!isTrustedPluginDashboardMessage(event)) return;
            var detail = event.data.detail || {};
            if (handleTokenBackedComplete(event.data, detail)) return;
            handleTokenlessPluginDashboardMessage(event.data, detail);
        }

        window.addEventListener('message', handler);
        return function () {
            window.removeEventListener('message', handler);
        };
    }

    function openModelManagerPage(lanlanName) {
        var name = typeof lanlanName === 'string' && lanlanName.trim()
            ? lanlanName.trim()
            : getTutorialModelManagerLanlanName();
        var url = '/model_manager?lanlan_name=' + encodeURIComponent(name);
        var prefix = getPrefix();
        var windowName = getModelManagerWindowName(name, prefix);
        return openPage(
            url,
            windowName,
            buildCenteredWindowFeatures(1280, 900)
        );
    }

    function triggerGoodbye(reason) {
        if (reason) {
            console.log('[YuiGuideHandoff] triggerGoodbye, reason:', reason);
        }
        window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
    }

    function triggerReturn() {
        var prefix = getPrefix();
        window.dispatchEvent(new CustomEvent(prefix + '-return-click'));
    }

    function cleanupTutorialPopups(event) {
        var detail = event && event.detail ? event.detail : null;
        if (detail && detail.page !== 'home') {
            return;
        }
        closeAgentPanel();
        closeSettingsPanel();
        clearHandoffToken();
    }

    function openPageWithHandoff(targetPage, resumeScene, openUrl, windowName, features, options) {
        var isPluginDashboardTarget = (
            targetPage === 'plugin_dashboard'
            || normalizeWindowName(windowName) === normalizeWindowName('plugin_dashboard')
        );
        if (isPluginDashboardTarget) {
            // Plugin dashboard uses the dedicated start / interrupt-ack flow managed by the guide director.
            var pluginDashboardOptions = Object.assign({}, options || {}, {
                forceReload: true
            });
            return openPage(
                buildPluginDashboardUrl(),
                windowName,
                features,
                pluginDashboardOptions
            );
        }

        var tokenObj = createHandoffToken(targetPage, resumeScene);
        if (!tokenObj) {
            console.warn('[YuiGuideHandoff] openPageWithHandoff: token 创建失败，回退到普通打开');
            return openPage(
                openUrl,
                windowName,
                features,
                options
            );
        }

        var targetUrl = openUrl;

        return openPage(targetUrl, windowName, features, options).then(function (childWin) {
            if (childWin) {
                dispatchHandoffSentEvent(tokenObj, targetPage, resumeScene);
                return childWin;
            }

            var currentTokenObj = readHandoffToken();
            if (
                currentTokenObj
                && !currentTokenObj.consumed
                && getHandoffTokenSignature(currentTokenObj) === getHandoffTokenSignature(tokenObj)
            ) {
                clearHandoffToken();
            }

            return null;
        });
    }

    window.addEventListener('storage', function (event) {
        if (event.key !== HANDOFF_CONSUMED_NOTIFY_KEY || !event.newValue) {
            return;
        }

        try {
            var payload = JSON.parse(event.newValue);
            if (!payload || payload.sessionId === HANDOFF_SESSION_ID) {
                return;
            }
            dispatchHandoffConsumedEvent(payload.detail || {});
        } catch (error) {
            console.warn('[YuiGuideHandoff] handoff_consumed storage payload 无法解析:', error);
        }
    });

    window.addEventListener('neko:yui-guide:tutorial-end', function (event) {
        if (!event || !event.detail || event.detail.page !== 'home') {
            return;
        }
        cleanupTutorialPopups(event);
    });

    var handoff = Object.freeze({
        // M1
        openPage: openPage,
        isWindowOpen: isWindowOpen,
        resumeOnReturn: resumeOnReturn,
        normalizeWindowName: normalizeWindowName,
        // M2
        openSettingsPanel: openSettingsPanel,
        closeSettingsPanel: closeSettingsPanel,
        openAgentPanel: openAgentPanel,
        closeAgentPanel: closeAgentPanel,
        ensureSettingsMenuVisible: ensureSettingsMenuVisible,
        triggerGoodbye: triggerGoodbye,
        triggerReturn: triggerReturn,
        cleanupTutorialPopups: cleanupTutorialPopups,
        ensureAgentToggleChecked: ensureAgentToggleChecked,
        setAgentMasterEnabled: setAgentMasterEnabled,
        setAgentFlagEnabled: setAgentFlagEnabled,
        ensureAgentSidePanelVisible: ensureAgentSidePanelVisible,
        ensureAgentSidePanelActionVisible: ensureAgentSidePanelActionVisible,
        clickAgentSidePanelAction: clickAgentSidePanelAction,
        createHandoffToken: createHandoffToken,
        readHandoffToken: readHandoffToken,
        consumeHandoffToken: consumeHandoffToken,
        clearHandoffToken: clearHandoffToken,
        getPluginDashboardExpectedOrigin: getPluginDashboardExpectedOrigin,
        openPageWithHandoff: openPageWithHandoff,
        openPluginDashboard: openPluginDashboard,
        listenPluginDashboardComplete: listenPluginDashboardComplete,
        openModelManagerPage: openModelManagerPage,
        waitForWindowOpen: waitForWindowOpen,
        closeWindow: closeWindow
    });

    function getHomeInteractionApi() {
        return handoff;
    }

    window.YuiGuidePageHandoff = handoff;
    window.YuiGuideHomeInteractionApi = handoff;
    window.getYuiGuideHomeInteractionApi = getHomeInteractionApi;
})();
