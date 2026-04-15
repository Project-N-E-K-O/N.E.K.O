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

    var handoff = Object.freeze({
        openPage: openPage,
        isWindowOpen: isWindowOpen,
        resumeOnReturn: resumeOnReturn,
        normalizeWindowName: normalizeWindowName
    });

    window.YuiGuidePageHandoff = handoff;
})();
