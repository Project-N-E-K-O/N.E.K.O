/**
 * app-exit-retention.js — visual-only goodbye/return animation hooks.
 *
 * This module deliberately does not own session, socket, lock, drag, or model
 * state. app-ui.js keeps those responsibilities and calls these hooks as an
 * optional visual layer.
 */
(function () {
    'use strict';

    const BUBBLE_ID = 'neko-exit-retention-bubble';
    const RETURN_CLASS = 'neko-return-ball-arriving';
    const STAY_EVENT = 'neko-exit-retention-stay';
    let bubbleTimer = null;
    let returnTimer = null;

    function translate(key) {
        if (typeof window.safeT === 'function') {
            const value = window.safeT(key, '');
            if (value && value !== key) return value;
        }
        if (typeof window.t === 'function') {
            const value = window.t(key);
            if (value && value !== key) return value;
        }
        return '';
    }

    function clearTimer(timerName) {
        if (timerName === 'bubble' && bubbleTimer) {
            clearTimeout(bubbleTimer);
            bubbleTimer = null;
        } else if (timerName === 'return' && returnTimer) {
            clearTimeout(returnTimer);
            returnTimer = null;
        }
    }

    function getRectCenter(rect) {
        if (!rect || typeof rect.left !== 'number' || typeof rect.top !== 'number') {
            return null;
        }
        const width = typeof rect.width === 'number' ? rect.width : 0;
        const height = typeof rect.height === 'number' ? rect.height : 0;
        return {
            x: rect.left + width / 2,
            y: rect.top + height / 2
        };
    }

    function resolveAnchorRect(options) {
        if (options && options.anchorRect) return options.anchorRect;
        if (options && options.returnButtonRect) return options.returnButtonRect;
        if (window._savedGoodbyeRect) return window._savedGoodbyeRect;
        return null;
    }

    function removeBubble() {
        clearTimer('bubble');
        const bubble = document.getElementById(BUBBLE_ID);
        if (bubble) bubble.remove();
    }

    function positionBubble(bubble, rect) {
        const center = getRectCenter(rect);
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        const fallbackX = Math.max(24, viewportWidth - 360);
        const fallbackY = Math.max(24, Math.round(viewportHeight * 0.28));
        const baseX = center ? center.x : fallbackX;
        const baseY = center ? center.y : fallbackY;
        const bubbleWidth = bubble.offsetWidth || 220;
        const bubbleHeight = bubble.offsetHeight || 64;
        const left = Math.max(16, Math.min(baseX - bubbleWidth - 22, viewportWidth - bubbleWidth - 16));
        const top = Math.max(16, Math.min(baseY - bubbleHeight - 18, viewportHeight - bubbleHeight - 16));

        bubble.style.left = `${Math.round(left)}px`;
        bubble.style.top = `${Math.round(top)}px`;
    }

    function showBubble(key, rect, options) {
        const text = translate(key);
        const opts = options || {};
        removeBubble();
        if (!text) return null;

        const bubble = document.createElement('div');
        bubble.id = BUBBLE_ID;
        bubble.className = 'neko-exit-retention-bubble';
        bubble.setAttribute('role', 'status');
        bubble.setAttribute('aria-live', 'polite');

        const message = document.createElement('span');
        message.className = 'neko-exit-retention-message';
        message.textContent = text;
        bubble.appendChild(message);

        if (opts.actionKey && opts.actionEvent) {
            const actionText = translate(opts.actionKey);
            if (actionText) {
                const actionButton = document.createElement('button');
                actionButton.type = 'button';
                actionButton.className = 'neko-exit-retention-action';
                actionButton.textContent = actionText;
                actionButton.addEventListener('click', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    window.dispatchEvent(new CustomEvent(opts.actionEvent, {
                        detail: opts.actionDetail || {}
                    }));
                    removeBubble();
                });
                bubble.classList.add('has-action');
                bubble.appendChild(actionButton);
            }
        }

        document.body.appendChild(bubble);

        positionBubble(bubble, rect);
        requestAnimationFrame(() => {
            if (bubble.isConnected) bubble.classList.add('is-visible');
        });

        bubbleTimer = setTimeout(() => {
            bubble.classList.remove('is-visible');
            bubble.classList.add('is-leaving');
            bubbleTimer = setTimeout(removeBubble, 220);
        }, opts.duration || 1500);

        return bubble;
    }

    function cleanup() {
        clearTimer('bubble');
        clearTimer('return');
        removeBubble();
        document.querySelectorAll('[id$="-return-button-container"]').forEach(el => {
            el.classList.remove(RETURN_CLASS);
        });
    }

    function playGoodbye(options) {
        cleanup();

        const opts = options || {};
        const anchorRect = resolveAnchorRect(opts);
        showBubble('exitRetention.goodbyeBubble', anchorRect, {
            actionKey: 'exitRetention.stayButton',
            actionEvent: STAY_EVENT,
            actionDetail: { returnButtonRect: anchorRect },
            duration: 1700
        });
    }

    function playReturn(options) {
        removeBubble();

        const opts = options || {};
        const returnRect = opts.returnButtonRect || opts.anchorRect || null;
        showBubble('exitRetention.returnBubble', returnRect);

        const returnContainers = document.querySelectorAll('[id$="-return-button-container"]');
        returnContainers.forEach(el => el.classList.add(RETURN_CLASS));
        returnTimer = setTimeout(() => {
            returnTimer = null;
            returnContainers.forEach(el => el.classList.remove(RETURN_CLASS));
        }, 700);
    }

    window.exitRetentionAnimation = {
        playGoodbye,
        playReturn,
        cleanup
    };
})();
