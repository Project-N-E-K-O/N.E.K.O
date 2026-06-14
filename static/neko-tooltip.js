/**
 * Lightweight shared tooltip helper.
 *
 * This module is intentionally business-agnostic: callers provide an element
 * and text, and the helper handles hover/focus display, positioning, and
 * cleanup with one reusable body-level tooltip node.
 */
(function () {
    'use strict';

    const DEFAULT_DELAY = 300;
    const DEFAULT_MAX_WIDTH = 320;
    const EDGE_MARGIN = 10;
    const TARGET_GAP = 8;

    const state = {
        tooltip: null,
        timer: null,
        target: null,
        cleanupByElement: new WeakMap()
    };

    function ensureStyles() {
        if (document.getElementById('neko-tooltip-styles')) return;
        const style = document.createElement('style');
        style.id = 'neko-tooltip-styles';
        style.textContent = `
            .neko-tooltip {
                position: fixed;
                z-index: 100060;
                max-width: var(--neko-tooltip-max-width, 320px);
                padding: 8px 10px;
                border-radius: 8px;
                border: 1px solid var(--neko-popup-border-color, rgba(0, 0, 0, 0.12));
                background: var(--neko-popup-bg, rgba(255, 255, 255, 0.96));
                color: var(--neko-popup-text, #222);
                box-shadow: var(--neko-popup-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.04));
                font-size: 12px;
                line-height: 1.45;
                white-space: pre-wrap;
                overflow-wrap: anywhere;
                pointer-events: none;
                opacity: 0;
                transform: translateY(2px);
                transition: opacity 0.12s ease, transform 0.12s ease;
            }
            .neko-tooltip.visible {
                opacity: 1;
                transform: translateY(0);
            }
        `;
        document.head.appendChild(style);
    }

    function clearTimer() {
        if (!state.timer) return;
        clearTimeout(state.timer);
        state.timer = null;
    }

    function getTooltipText(element, options) {
        const source = options && options.text;
        if (typeof source === 'function') return String(source(element) || '').trim();
        return String(source || element.dataset.nekoTooltipText || element.title || '').trim();
    }

    function shouldShow(element, text, options) {
        if (!element || !text) return false;
        if (options && options.onlyIfOverflow === false) return true;
        const visibleText = String(element.textContent || '').trim();
        // Callers may shorten text before rendering, so compare visible text
        // with the full tooltip text in addition to checking CSS overflow.
        return element.scrollWidth > element.clientWidth + 1 || (!!visibleText && visibleText !== text);
    }

    // Body-level placement avoids clipping by containers that use overflow or contain.
    // The tooltip is positioned once per show instead of following mousemove.
    function positionTooltip(element, tooltip) {
        const rect = element.getBoundingClientRect();
        const tipRect = tooltip.getBoundingClientRect();
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;

        let left = rect.left + rect.width / 2 - tipRect.width / 2;
        left = Math.max(EDGE_MARGIN, Math.min(left, Math.max(EDGE_MARGIN, viewportWidth - tipRect.width - EDGE_MARGIN)));

        let top = rect.top - tipRect.height - TARGET_GAP;
        if (top < EDGE_MARGIN) {
            top = rect.bottom + TARGET_GAP;
        }
        top = Math.max(EDGE_MARGIN, Math.min(top, Math.max(EDGE_MARGIN, viewportHeight - tipRect.height - EDGE_MARGIN)));

        tooltip.style.left = Math.round(left) + 'px';
        tooltip.style.top = Math.round(top) + 'px';
    }

    function hide() {
        clearTimer();
        if (state.tooltip) {
            state.tooltip.classList.remove('visible');
            state.tooltip.remove();
            state.tooltip = null;
        }
        state.target = null;
    }

    function show(element, options) {
        const text = getTooltipText(element, options || {});
        if (!shouldShow(element, text, options || {})) return;

        hide();
        state.target = element;
        state.timer = setTimeout(() => {
            state.timer = null;
            if (!document.body || !document.body.contains(element)) return;

            ensureStyles();
            const tooltip = document.createElement('div');
            tooltip.className = 'neko-tooltip';
            tooltip.textContent = text;
            tooltip.style.setProperty('--neko-tooltip-max-width', ((options && options.maxWidth) || DEFAULT_MAX_WIDTH) + 'px');
            document.body.appendChild(tooltip);
            state.tooltip = tooltip;

            positionTooltip(element, tooltip);
            requestAnimationFrame(() => {
                if (state.tooltip === tooltip) tooltip.classList.add('visible');
            });
        }, (options && typeof options.delay === 'number') ? options.delay : DEFAULT_DELAY);
    }

    /**
     * Attach a shared tooltip to an element.
     *
     * options.text may be a string or function. By default the custom tooltip
     * only appears when the element is visually truncated or the visible text is
     * shortened. The native title is cleared so browsers do not show a second
     * tooltip beside this custom one.
     */
    function attach(element, options) {
        if (!element) return;
        const opts = options || {};
        const text = getTooltipText(element, opts);
        element.dataset.nekoTooltipText = text;
        element.removeAttribute('title');

        const previous = state.cleanupByElement.get(element);
        if (previous) previous();

        const onEnter = () => show(element, opts);
        const onLeave = () => hide();
        const onFocus = () => show(element, opts);
        const onBlur = () => hide();

        element.addEventListener('mouseenter', onEnter);
        element.addEventListener('mouseleave', onLeave);
        element.addEventListener('focus', onFocus);
        element.addEventListener('blur', onBlur);

        // Explicit cleanup keeps detached HUD cards from leaving tooltip state behind.
        state.cleanupByElement.set(element, () => {
            element.removeEventListener('mouseenter', onEnter);
            element.removeEventListener('mouseleave', onLeave);
            element.removeEventListener('focus', onFocus);
            element.removeEventListener('blur', onBlur);
            if (state.target === element) hide();
        });
    }

    /**
     * Remove tooltip listeners/state for an element.
     *
     * This does not restore title; custom tooltip callers intentionally suppress
     * native browser tooltips to avoid double popups.
     */
    function destroyFor(element) {
        const cleanup = state.cleanupByElement.get(element);
        if (!cleanup) return;
        cleanup();
        state.cleanupByElement.delete(element);
    }

    window.NekoTooltip = {
        attach,
        hide,
        destroyFor
    };
})();
