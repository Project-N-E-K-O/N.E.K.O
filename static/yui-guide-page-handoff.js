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

    var _activeWindows = {};
    var _activeTimers = {};

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
    function openPage(openUrl, windowName, features) {
        var fullName = normalizeWindowName(windowName);
        if (!fullName) {
            console.warn('[YuiGuideHandoff] windowName 为空，取消打开');
            return Promise.resolve(null);
        }
        var childWin;

        if (typeof window.openOrFocusWindow === 'function') {
            childWin = window.openOrFocusWindow(openUrl, fullName, features);
        } else {
            childWin = window.open(openUrl, fullName, features);
        }

        if (!childWin) {
            console.warn('[YuiGuideHandoff] 窗口打开失败或被拦截:', openUrl);
            return Promise.resolve(null);
        }

        _activeWindows[fullName] = childWin;

        if (typeof window.handleHideMainUI === 'function') {
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
                if (typeof onReturn === 'function') onReturn();
            }
        }, WINDOW_CHECK_INTERVAL_MS);
    }

    // ─── 内部：弹层工具 ──────────────────────────────────────

    var POPUP_OPEN_ANIMATION_MS = 250;
    var _popupsOpenedByTutorial = {};

    // ─── M3: Handoff Token 常量 ──────────────────────────────

    var HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token';
    var HANDOFF_CONSUMED_NOTIFY_KEY = 'neko_yui_guide_handoff_consumed';
    var HANDOFF_TOKEN_VERSION = 1;
    var HANDOFF_TOKEN_TTL_MS = 5 * 60 * 1000; // 5 分钟
    var HANDOFF_FLOW_ID = 'home_yui_guide_v1';

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

    // ─── M3: Handoff Token CRUD ──────────────────────────────

    /**
     * 生成简易唯一 ID。
     */
    function generateTokenId() {
        return 'h_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 10);
    }

    var HANDOFF_SESSION_ID = generateTokenId();

    function getHandoffTokenSignature(tokenObj) {
        if (!tokenObj) return '';
        return tokenObj.signature || tokenObj.id || tokenObj.token || '';
    }

    function dispatchHandoffConsumedEvent(detail) {
        var payload = detail || {};
        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-consumed', {
            detail: payload
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
        } catch (e) {
            console.warn('[YuiGuideHandoff] notifyHandoffConsumed: 广播失败:', e);
        }
    }

    /**
     * 创建 handoff token 并写入 localStorage。
     *
     * @param {string} targetPage - 目标页面标识，如 'api_key'
     * @param {string} [resumeScene] - 恢复场景 ID，如 'handoff_api_key'，可为 null
     * @returns {Object|null} token 对象，失败返回 null
     */
    function createHandoffToken(targetPage, resumeScene) {
        var now = Date.now();
        var tokenObj = {
            token: generateTokenId(),
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
        } catch (e) {
            console.error('[YuiGuideHandoff] createHandoffToken: 存储失败:', e);
            return null;
        }
        return tokenObj;
    }

    /**
     * 读取并校验 handoff token（不消费）。
     *
     * @returns {Object|null} 有效 token 对象，无则返回 null
     */
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
        } catch (e) {
            console.error('[YuiGuideHandoff] readHandoffToken: 读取失败:', e);
            return null;
        }
    }

    /**
     * 消费 handoff token（单次语义：读取 + 校验页面 + 清除）。
     *
     * @param {string} [expectedPage] - 期望的目标页面标识，不匹配则不消费
     * @returns {Object|null} 有效 token 对象，失败或不匹配返回 null
     */
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
        } catch (e) {
            console.error('[YuiGuideHandoff] consumeHandoffToken: 标记消费失败:', e);
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

    /**
     * 清除 localStorage 中的 handoff token。
     */
    function clearHandoffToken() {
        try {
            localStorage.removeItem(HANDOFF_STORAGE_KEY);
        } catch (e) { /* ignore */ }
    }

    /**
     * 打开目标页面并携带 handoff token。
     * token 创建失败时回退到普通打开。
     *
     * @param {string} targetPage - 目标页面标识
     * @param {string} [resumeScene] - 恢复场景 ID
     * @param {string} openUrl - 目标页面 URL
     * @param {string} windowName - 窗口名称简写
     * @param {string} [features] - window.open features
     * @returns {Promise<Window|null>}
     */
    function openPageWithHandoff(targetPage, resumeScene, openUrl, windowName, features) {
        var tokenObj = createHandoffToken(targetPage, resumeScene);
        if (!tokenObj) {
            console.warn('[YuiGuideHandoff] openPageWithHandoff: token 创建失败，回退到普通打开');
            return openPage(openUrl, windowName, features);
        }

        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-sent', {
            detail: {
                token: tokenObj.token,
                target_page: targetPage,
                resume_scene: resumeScene
            }
        }));

        return openPage(openUrl, windowName, features).then(function (childWin) {
            if (childWin) {
                return childWin;
            }

            var currentTokenObj = readHandoffToken();
            if (
                currentTokenObj &&
                !currentTokenObj.consumed &&
                getHandoffTokenSignature(currentTokenObj) === getHandoffTokenSignature(tokenObj)
            ) {
                clearHandoffToken();
            }

            return null;
        });
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

        if (!manager || !popup || typeof manager.showPopup !== 'function') {
            console.warn('[YuiGuideHandoff] openSettingsPanel: manager/showPopup 不可用');
            return Promise.resolve(false);
        }

        if (popup.style.display === 'flex') {
            return Promise.resolve(true);
        }

        manager.showPopup('settings', popup);

        return new Promise(function (resolve) {
            setTimeout(function () {
                if (popup.style.display === 'flex') {
                    _popupsOpenedByTutorial['settings'] = true;
                }
                resolve(popup.style.display === 'flex');
            }, POPUP_OPEN_ANIMATION_MS);
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
        manager.closePopupById('settings');
        delete _popupsOpenedByTutorial['settings'];
        var popup = getPopup('settings');
        var closed = !popup || popup.style.display !== 'flex';
        return Promise.resolve(closed);
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

        if (!manager || !popup || typeof manager.showPopup !== 'function') {
            console.warn('[YuiGuideHandoff] openAgentPanel: manager/showPopup 不可用');
            return Promise.resolve(false);
        }

        if (popup.style.display === 'flex') {
            return Promise.resolve(true);
        }

        manager.showPopup('agent', popup);

        return new Promise(function (resolve) {
            setTimeout(function () {
                if (popup.style.display === 'flex') {
                    _popupsOpenedByTutorial['agent'] = true;
                }
                resolve(popup.style.display === 'flex');
            }, POPUP_OPEN_ANIMATION_MS);
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

    // ─── M2: "请她离开/回来" 包装 ────────────────────────────

    /**
     * 触发 Yui 离开流程。
     * 所有模型类型（Live2D/VRM/MMD）的 goodbye 按钮统一派发 'live2d-goodbye-click'，
     * 此处保持一致。
     *
     * @param {string} [reason] - 可选离开原因，用于日志
     */
    function triggerGoodbye(reason) {
        if (reason) {
            console.log('[YuiGuideHandoff] triggerGoodbye, reason:', reason);
        }
        window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
    }

    /**
     * 触发 Yui 回来流程。
     * 包装现有的 ${prefix}-return-click 事件。
     */
    function triggerReturn() {
        var prefix = getPrefix();
        window.dispatchEvent(new CustomEvent(prefix + '-return-click'));
    }

    // ─── M2: 教程结束清理 ────────────────────────────────────

    /**
     * 关闭教程期间打开的所有弹层，恢复页面干净状态。
     */
    function cleanupTutorialPopups() {
        var manager = getManager();
        clearHandoffToken();

        if (manager && typeof manager.closePopupById === 'function') {
            Object.keys(_popupsOpenedByTutorial).forEach(function (buttonId) {
                manager.closePopupById(buttonId);
            });
        }
        _popupsOpenedByTutorial = {};
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
        } catch (e) {
            console.warn('[YuiGuideHandoff] handoff_consumed storage payload 无法解析:', e);
        }
    });

    window.addEventListener('neko:yui-guide:tutorial-end', function () {
        cleanupTutorialPopups();
    });

    // ─── 导出 ─────────────────────────────────────────────────

    var handoff = Object.freeze({
        // M1
        openPage: openPage,
        isWindowOpen: isWindowOpen,
        resumeOnReturn: resumeOnReturn,
        normalizeWindowName: normalizeWindowName,
        // M2 — 弹层
        openSettingsPanel: openSettingsPanel,
        closeSettingsPanel: closeSettingsPanel,
        openAgentPanel: openAgentPanel,
        ensureSettingsMenuVisible: ensureSettingsMenuVisible,
        // M2 — 离开/回来
        triggerGoodbye: triggerGoodbye,
        triggerReturn: triggerReturn,
        // M2 — 清理
        cleanupTutorialPopups: cleanupTutorialPopups,
        // M3 — 跨页 handoff
        createHandoffToken: createHandoffToken,
        readHandoffToken: readHandoffToken,
        consumeHandoffToken: consumeHandoffToken,
        clearHandoffToken: clearHandoffToken,
        openPageWithHandoff: openPageWithHandoff
    });

    window.YuiGuidePageHandoff = handoff;
})();
