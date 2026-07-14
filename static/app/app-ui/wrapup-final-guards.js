/**
 * app-ui/wrapup-final-guards.js
 * UI display helpers extracted from app.js.
 *
 * Exposed as window.appUi.
 * Dependencies:
 * - window.appState (S) - shared mutable state
 * - window.appConst (C) - frozen constants
 * - window.appUtils - utility helpers
 * - window.t / window.safeT - i18n
 * - window.lanlan_config - character config
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appUi = window.appUi || {};
    const I = window.__appUiParts || (window.__appUiParts = {});
    function ensureHiddenElements() {
        const elementsToHide = [
            document.getElementById('sidebar'),
            document.getElementById('sidebarbox'),
            document.getElementById('status')
        ].filter(Boolean);

        elementsToHide.forEach(element => {
            if (element) {
                element.style.setProperty('display', 'none', 'important');
                element.style.setProperty('visibility', 'hidden', 'important');
            }
        });
    }

    I.mod.ensureHiddenElements = ensureHiddenElements;

    /**
     * Set up MutationObserver to keep sidebar/sidebarbox/status hidden,
     * and register beforeunload cleanup.
     * Called once during init.
     */
    function initFinalUiGuards() {
        // 立即执行一次
        ensureHiddenElements();

        // MutationObserver
        const observerCallback = (mutations) => {
            let needsHiding = false;
            mutations.forEach(mutation => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                    const target = mutation.target;
                    const computedStyle = window.getComputedStyle(target);
                    if (computedStyle.display !== 'none' || computedStyle.visibility !== 'hidden') {
                        needsHiding = true;
                    }
                }
            });

            if (needsHiding) {
                ensureHiddenElements();
            }
        };

        const observer = new MutationObserver(observerCallback);

        const elementsToObserve = [
            document.getElementById('sidebar'),
            document.getElementById('sidebarbox'),
            document.getElementById('status')
        ].filter(Boolean);

        elementsToObserve.forEach(element => {
            observer.observe(element, {
                attributes: true,
                attributeFilter: ['style']
            });
        });

        // beforeunload cleanup 已在 app.js orchestrator 中注册，此处不再重复
    }

    I.mod.initFinalUiGuards = initFinalUiGuards;

    // ================================================================
    //  向后兼容 window.xxx 全局导出
    // ================================================================
    // showStatusToast / showProminentNotice 已在上方直接赋值
    window.showVoicePreparingToast = I.showVoicePreparingToast;
    window.hideVoicePreparingToast = I.hideVoicePreparingToast;
    window.showReadyToSpeakToast = I.showReadyToSpeakToast;
    window.syncFloatingMicButtonState = I.syncFloatingMicButtonState;
    window.syncFloatingScreenButtonState = I.syncFloatingScreenButtonState;
    window.hideLive2d = I.hideLive2d;
    window.showLive2d = I.showLive2d;
    window.showCurrentModel = I.showCurrentModel;
    window.ensureHiddenElements = ensureHiddenElements;

    // ================================================================
    //  Publish module
    // ================================================================
    Object.assign(window.appUi, I.mod || {});
    delete window.__appUiParts;
})();
