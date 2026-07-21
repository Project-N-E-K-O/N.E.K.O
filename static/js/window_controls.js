(function () {
    'use strict';

    const CONTROL_SELECTOR = '[data-neko-window-control]';
    const MAXIMIZE_ICON_SELECTOR = '.neko-window-maximize-icon';
    const PIN_ICON_SELECTOR = '.neko-window-pin-icon';
    const NATIVE_DRAG_SOURCE_SELECTOR = 'a[href], img, svg, video, audio';

    function translate(key, fallback) {
        const translators = [];
        try {
            if (typeof window.t === 'function') {
                translators.push({ fn: window.t, owner: window });
            }
        } catch (error) {
            // 当前窗口未加载 i18n 时继续尝试同源 opener
        }
        try {
            if (window.opener && window.opener !== window && typeof window.opener.t === 'function') {
                translators.push({ fn: window.opener.t, owner: window.opener });
            }
        } catch (error) {
            // 跨域 opener 不可访问时使用兜底文案
        }
        for (const translator of translators) {
            try {
                const value = translator.fn.call(translator.owner, key);
                if (typeof value === 'string' && value && value !== key) {
                    return value;
                }
            } catch (error) {
                // 某个翻译源未就绪时继续尝试下一个
            }
        }
        return fallback;
    }

    function setButtonLabel(button, key, fallback) {
        if (!button) return;
        const label = translate(key, fallback);
        button.setAttribute('data-i18n-title', key);
        button.setAttribute('data-i18n-aria', key);
        button.setAttribute('title', label);
        button.setAttribute('aria-label', label);
    }

    function updateMaximizeState(isMaximized) {
        const maximizeButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="maximize"]`);
        const icon = maximizeButton ? maximizeButton.querySelector(MAXIMIZE_ICON_SELECTOR) : null;
        const root = document.documentElement;
        const body = document.body;
        if (root) {
            root.classList.toggle('neko-window-maximized', !!isMaximized);
        }
        if (body) {
            body.classList.toggle('neko-window-maximized', !!isMaximized);
        }
        if (icon) {
            icon.classList.toggle('restored', !!isMaximized);
        }
        setButtonLabel(
            maximizeButton,
            isMaximized ? 'common.restore' : 'common.maximize',
            isMaximized ? '恢复' : '最大化'
        );
    }

    async function refreshMaximizeState() {
        const api = window.nekoWindowControl;
        if (!api || typeof api.isMaximized !== 'function') return;
        try {
            const isMaximized = await api.isMaximized();
            updateMaximizeState(isMaximized);
        } catch (error) {
            // 非 Electron 环境下忽略
        }
    }

    function updatePinState(state) {
        const pinButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        if (!pinButton) return;
        const allowed = !!(state && state.allowed);
        const pinned = allowed && !!state.pinned;
        pinButton.hidden = !allowed;
        pinButton.classList.toggle('is-pinned', pinned);
        pinButton.setAttribute('aria-pressed', pinned ? 'true' : 'false');
        const icon = pinButton.querySelector(PIN_ICON_SELECTOR);
        if (icon) icon.classList.toggle('pinned', pinned);
        setButtonLabel(
            pinButton,
            pinned ? 'common.unpinWindow' : 'common.pinWindow',
            pinned ? 'Unpin window' : 'Pin window'
        );
        if (pinButton.hasAttribute('data-tooltip')) {
            pinButton.setAttribute('data-tooltip', pinButton.getAttribute('title') || '');
        }
    }

    async function refreshPinState() {
        const pinButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        if (!pinButton) return;
        const api = window.nekoWindowControl;
        if (!api || typeof api.getAlwaysOnTopState !== 'function') {
            pinButton.hidden = true;
            return;
        }
        try {
            updatePinState(await api.getAlwaysOnTopState());
        } catch (error) {
            pinButton.hidden = true;
        }
    }

    function bindMinimizeButton() {
        const minimizeButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="minimize"]`);
        if (!minimizeButton || minimizeButton.dataset.nekoWindowControlBound === '1') return;
        minimizeButton.dataset.nekoWindowControlBound = '1';
        minimizeButton.addEventListener('click', async () => {
            if (minimizeButton.disabled) return;
            const api = window.nekoWindowControl;
            if (!api || typeof api.minimize !== 'function') return;
            try {
                await api.minimize();
            } catch (error) {
                // 非 Electron 环境下忽略
            }
        });
    }

    function bindMaximizeButton() {
        const maximizeButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="maximize"]`);
        if (!maximizeButton || maximizeButton.dataset.nekoWindowControlBound === '1') return;
        maximizeButton.dataset.nekoWindowControlBound = '1';
        maximizeButton.addEventListener('click', async () => {
            if (maximizeButton.disabled) return;
            const api = window.nekoWindowControl;
            if (!api || typeof api.maximize !== 'function') return;
            try {
                const result = await api.maximize();
                if (result && result.ok) {
                    updateMaximizeState(result.isMaximized);
                }
            } catch (error) {
                // 非 Electron 环境下忽略
            }
        });
    }

    function bindPinButton() {
        const pinButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        if (!pinButton || pinButton.dataset.nekoWindowControlBound === '1') return;
        pinButton.dataset.nekoWindowControlBound = '1';
        pinButton.addEventListener('click', async () => {
            if (pinButton.disabled) return;
            const api = window.nekoWindowControl;
            if (!api || typeof api.toggleAlwaysOnTop !== 'function') return;
            pinButton.disabled = true;
            try {
                updatePinState(await api.toggleAlwaysOnTop());
            } catch (error) {
                await refreshPinState();
            } finally {
                pinButton.disabled = false;
            }
        });
    }

    function defaultCloseCurrentWindow() {
        try {
            window.close();
        } catch (error) {
            // 某些浏览器环境会拒绝关闭非脚本打开的页面
        }
        window.setTimeout(() => {
            if (window.closed) return;
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        }, 120);
    }

    async function closeCurrentWindow() {
        try {
            if (typeof window.nekoBeforeWindowClose === 'function') {
                const result = await window.nekoBeforeWindowClose();
                if (result === false || (result && result.handled === true)) {
                    return;
                }
            }
        } catch (error) {
            // 页面自定义关闭逻辑失败时回退到默认关闭
        }
        defaultCloseCurrentWindow();
    }

    function bindCloseButton() {
        const closeButton = document.querySelector(`${CONTROL_SELECTOR}[data-neko-window-control="close"]`);
        if (!closeButton || closeButton.dataset.nekoWindowControlBound === '1') return;
        closeButton.dataset.nekoWindowControlBound = '1';
        closeButton.addEventListener('click', (event) => {
            event.preventDefault();
            if (closeButton.disabled) return;
            void closeCurrentWindow();
        });
    }

    function initWindowControls() {
        bindPinButton();
        bindMinimizeButton();
        bindMaximizeButton();
        bindCloseButton();
        refreshMaximizeState();
        refreshPinState();
        if (!window.__nekoWindowControlsResizeBound) {
            window.__nekoWindowControlsResizeBound = true;
            window.addEventListener('resize', refreshMaximizeState);
        }
        if (!window.__nekoWindowControlsFocusBound) {
            window.__nekoWindowControlsFocusBound = true;
            window.addEventListener('focus', () => {
                refreshMaximizeState();
                refreshPinState();
            });
        }
        if (!window.__nekoWindowControlsMutationObserver && document.documentElement) {
            const observer = new MutationObserver((mutations) => {
                const hasNewControls = mutations.some((mutation) => Array.from(mutation.addedNodes || []).some((node) => {
                    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
                    if (node.matches && node.matches(CONTROL_SELECTOR)) return true;
                    return !!(node.querySelector && node.querySelector(CONTROL_SELECTOR));
                }));
                if (hasNewControls) initWindowControls();
            });
            observer.observe(document.documentElement, { childList: true, subtree: true });
            window.__nekoWindowControlsMutationObserver = observer;
        }
    }

    function initNativeDragGuard() {
        if (window.__nekoNativeDragGuardBound) return;
        window.__nekoNativeDragGuardBound = true;

        document.addEventListener('dragstart', (event) => {
            const rawTarget = event.target;
            let targetEl = null;
            if (rawTarget && rawTarget.nodeType === Node.ELEMENT_NODE) {
                targetEl = rawTarget;
            } else if (rawTarget && rawTarget.parentElement) {
                targetEl = rawTarget.parentElement;
            }

            if (!targetEl || typeof targetEl.closest !== 'function') return;
            const source = targetEl.closest(NATIVE_DRAG_SOURCE_SELECTOR);
            if (!source) return;
            event.preventDefault();
        }, true);
    }

    async function restoreCurrentWindowFromOpener() {
        const api = window.nekoWindowControl;
        if (!api || typeof api.restore !== 'function') return;
        try {
            await api.restore();
            await refreshMaximizeState();
        } catch (error) {
            // 非 Electron 环境下忽略
        }
    }

    window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) return;
        if (!event.data || event.data.type !== 'neko:restore-window') return;
        restoreCurrentWindowFromOpener();
    });

    window.nekoWindowControls = Object.assign({}, window.nekoWindowControls || {}, {
        init: initWindowControls,
        refresh: () => {
            refreshMaximizeState();
            refreshPinState();
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initWindowControls();
            initNativeDragGuard();
        });
    } else {
        initWindowControls();
        initNativeDragGuard();
    }
})();
