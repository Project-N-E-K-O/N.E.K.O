(function () {
    'use strict';

    const CONTROL_SELECTOR = '[data-neko-window-control]';
    const MAXIMIZE_ICON_SELECTOR = '.neko-window-maximize-icon';
    const NATIVE_DRAG_SOURCE_SELECTOR = 'a[href], img, svg, video, audio';
    const PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750];
    let pinStateRefreshGeneration = 0;

    function translate(key, fallback) {
        try {
            if (window.t) {
                const value = window.t(key);
                if (typeof value === 'string' && value && value !== key) {
                    return value;
                }
            }
        } catch (error) {
            // i18n 未就绪时使用兜底文案
        }
        return fallback;
    }

    function getButtonLabelFallback(button, key, fallback) {
        if (!button) return fallback;
        if (key === 'common.pinWindow') {
            return button.getAttribute('data-neko-pin-label') || fallback;
        }
        if (key === 'common.unpinWindow') {
            return button.getAttribute('data-neko-unpin-label') || fallback;
        }
        return fallback;
    }

    function setButtonLabel(button, key, fallback) {
        if (!button) return;
        const label = translate(key, getButtonLabelFallback(button, key, fallback));
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

    function updatePinState(state) {
        const pinButtons = document.querySelectorAll(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        if (!pinButtons.length) return;

        const available = !!(state && state.available);
        const pinned = available && !!state.pinned;
        pinButtons.forEach((pinButton) => {
            pinButton.hidden = !available;
            pinButton.classList.toggle('is-pinned', pinned);
            pinButton.setAttribute('aria-pressed', pinned ? 'true' : 'false');
            setButtonLabel(
                pinButton,
                pinned ? 'common.unpinWindow' : 'common.pinWindow',
                pinned ? '取消置顶' : '置顶窗口'
            );
        });
    }

    function schedulePinStateRefreshRetry(generation, retryIndex) {
        if (generation !== pinStateRefreshGeneration || retryIndex >= PIN_STATE_RETRY_DELAYS_MS.length) {
            return;
        }
        window.setTimeout(() => {
            if (generation !== pinStateRefreshGeneration) return;
            void refreshPinState({ generation, retryIndex: retryIndex + 1 });
        }, PIN_STATE_RETRY_DELAYS_MS[retryIndex]);
    }

    async function refreshPinState(retryContext) {
        const pinButtons = document.querySelectorAll(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        if (!pinButtons.length) return;
        const isRetry = !!(
            retryContext
            && retryContext.generation === pinStateRefreshGeneration
            && Number.isInteger(retryContext.retryIndex)
        );
        const generation = isRetry ? retryContext.generation : ++pinStateRefreshGeneration;
        const retryIndex = isRetry ? retryContext.retryIndex : 0;
        const api = window.nekoWindowControl;
        if (!api || typeof api.getPinState !== 'function') {
            updatePinState({ available: false, pinned: false });
            schedulePinStateRefreshRetry(generation, retryIndex);
            return;
        }
        try {
            const state = await api.getPinState();
            if (generation !== pinStateRefreshGeneration) return;
            const normalizedState = state || { available: false, pinned: false };
            updatePinState(normalizedState);
            if (!normalizedState.available) {
                schedulePinStateRefreshRetry(generation, retryIndex);
            }
        } catch (error) {
            if (generation !== pinStateRefreshGeneration) return;
            updatePinState({ available: false, pinned: false });
            schedulePinStateRefreshRetry(generation, retryIndex);
        }
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
        const pinButtons = document.querySelectorAll(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`);
        pinButtons.forEach((pinButton) => {
            if (pinButton.dataset.nekoWindowControlBound === '1') return;
            pinButton.dataset.nekoWindowControlBound = '1';
            pinButton.addEventListener('click', async () => {
                if (pinButton.disabled) return;
                const api = window.nekoWindowControl;
                if (!api || typeof api.togglePin !== 'function') return;
                pinButtons.forEach((button) => { button.disabled = true; });
                try {
                    const state = await api.togglePin();
                    ++pinStateRefreshGeneration;
                    updatePinState(state || { available: false, pinned: false });
                } catch (error) {
                    await refreshPinState();
                } finally {
                    pinButtons.forEach((button) => { button.disabled = false; });
                }
            });
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
        refreshPinState();
        refreshMaximizeState();
        if (!window.__nekoWindowControlsResizeBound) {
            window.__nekoWindowControlsResizeBound = true;
            window.addEventListener('resize', refreshMaximizeState);
        }
        if (!window.__nekoWindowControlsFocusBound) {
            window.__nekoWindowControlsFocusBound = true;
            window.addEventListener('focus', () => refreshPinState());
        }
        if (!window.__nekoWindowControlsLocaleBound) {
            window.__nekoWindowControlsLocaleBound = true;
            window.addEventListener('localechange', () => {
                refreshPinState();
                refreshMaximizeState();
            });
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
        refresh: () => Promise.all([refreshPinState(), refreshMaximizeState()])
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
