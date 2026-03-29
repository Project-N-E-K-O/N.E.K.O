/**
 * app-react-chat-window.js
 * Host-side controller for the exported React chat window.
 * - Dynamically loads the React bundle if needed
 * - Opens/closes the overlay window
 * - Makes the shell draggable on desktop
 * - Persists and restores window position
 */
(function () {
    'use strict';

    var BUNDLE_SRC = '/static/react/neko-chat/neko-chat-window.iife.js';
    var STORAGE_LEFT_KEY = 'neko.reactChatWindow.left';
    var STORAGE_TOP_KEY = 'neko.reactChatWindow.top';
    var loadedPromise = null;
    var mounted = false;
    var dragState = null;

    function $(id) {
        return document.getElementById(id);
    }

    function isMobileWidth() {
        return window.innerWidth <= 768;
    }

    function getOverlay() {
        return $('react-chat-window-overlay');
    }

    function getShell() {
        return $('react-chat-window-shell');
    }

    function getHeader() {
        return $('react-chat-window-host-header');
    }

    function getRoot() {
        return $('react-chat-window-root');
    }

    function getChatProps() {
        var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || 'N.E.K.O';
        return {
            title: lanlanName + ' Chat',
            subtitle: 'QQ-style chat window preview',
            status: 'Legacy Host Preview'
        };
    }

    function showToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration || 3000);
        }
    }

    function ensureBundleLoaded() {
        if (window.NekoChatWindow && typeof window.NekoChatWindow.mountChatWindow === 'function') {
            return Promise.resolve(window.NekoChatWindow);
        }

        if (loadedPromise) return loadedPromise;

        loadedPromise = new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[data-react-chat-window-bundle="true"]');
            if (existing) {
                existing.addEventListener('load', function () {
                    if (window.NekoChatWindow && typeof window.NekoChatWindow.mountChatWindow === 'function') {
                        resolve(window.NekoChatWindow);
                    } else {
                        reject(new Error('React chat bundle loaded but API is missing'));
                    }
                }, { once: true });
                existing.addEventListener('error', function () {
                    reject(new Error('React chat bundle failed to load'));
                }, { once: true });
                return;
            }

            var script = document.createElement('script');
            script.src = BUNDLE_SRC + '?v=' + Date.now();
            script.async = true;
            script.dataset.reactChatWindowBundle = 'true';

            script.onload = function () {
                if (window.NekoChatWindow && typeof window.NekoChatWindow.mountChatWindow === 'function') {
                    resolve(window.NekoChatWindow);
                } else {
                    reject(new Error('React chat bundle loaded but API is missing'));
                }
            };

            script.onerror = function () {
                reject(new Error('React chat bundle failed to load'));
            };

            document.body.appendChild(script);
        }).catch(function (error) {
            loadedPromise = null;
            throw error;
        });

        return loadedPromise;
    }

    function getStoredPosition() {
        try {
            var left = Number(localStorage.getItem(STORAGE_LEFT_KEY));
            var top = Number(localStorage.getItem(STORAGE_TOP_KEY));
            if (Number.isFinite(left) && Number.isFinite(top)) {
                return { left: left, top: top };
            }
        } catch (_) {}
        return null;
    }

    function persistPosition(left, top) {
        try {
            localStorage.setItem(STORAGE_LEFT_KEY, String(Math.round(left)));
            localStorage.setItem(STORAGE_TOP_KEY, String(Math.round(top)));
        } catch (_) {}
    }

    function clampPosition(left, top) {
        var shell = getShell();
        if (!shell) {
            return { left: left, top: top };
        }

        var rect = shell.getBoundingClientRect();
        var width = rect.width || 960;
        var headerHeight = 52;
        var maxLeft = Math.max(0, window.innerWidth - width);
        var maxTop = Math.max(0, window.innerHeight - headerHeight);

        return {
            left: Math.max(0, Math.min(maxLeft, left)),
            top: Math.max(0, Math.min(maxTop, top))
        };
    }

    function applyPosition(left, top) {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var clamped = clampPosition(left, top);
        shell.style.left = clamped.left + 'px';
        shell.style.top = clamped.top + 'px';
        shell.style.transform = 'none';
    }

    function centerWindow() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        var left = Math.max(0, Math.round((window.innerWidth - rect.width) / 2));
        var top = Math.max(0, Math.round((window.innerHeight - rect.height) / 2));
        applyPosition(left, top);
        persistPosition(left, top);
    }

    function restorePosition() {
        var shell = getShell();
        if (!shell) return;

        if (isMobileWidth()) {
            shell.style.removeProperty('left');
            shell.style.removeProperty('top');
            shell.style.removeProperty('transform');
            return;
        }

        var stored = getStoredPosition();
        if (stored) {
            applyPosition(stored.left, stored.top);
        } else {
            centerWindow();
        }
    }

    function mountWindow() {
        var root = getRoot();
        if (!root) return false;

        window.NekoChatWindow.mountChatWindow(root, getChatProps());
        mounted = true;
        return true;
    }

    function openWindow() {
        var overlay = getOverlay();
        if (!overlay) return;

        ensureBundleLoaded()
            .then(function () {
                if (!mountWindow()) {
                    showToast('新版聊天框挂载失败', 3000);
                    return;
                }
                overlay.hidden = false;
                document.body.classList.add('react-chat-window-open');
                restorePosition();
            })
            .catch(function (error) {
                console.error('[ReactChatWindow] open failed:', error);
                showToast('新版聊天框资源加载失败', 3500);
            });
    }

    function closeWindow() {
        var overlay = getOverlay();
        if (!overlay) return;
        overlay.hidden = true;
        document.body.classList.remove('react-chat-window-open');
    }

    function startDrag(clientX, clientY) {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        dragState = {
            pointerOffsetX: clientX - rect.left,
            pointerOffsetY: clientY - rect.top
        };

        shell.classList.add('is-dragging');
        document.body.classList.add('react-chat-window-dragging');
    }

    function updateDrag(clientX, clientY) {
        if (!dragState) return;

        var left = clientX - dragState.pointerOffsetX;
        var top = clientY - dragState.pointerOffsetY;
        var clamped = clampPosition(left, top);
        applyPosition(clamped.left, clamped.top);
    }

    function stopDrag() {
        if (!dragState) return;

        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-dragging');
            var rect = shell.getBoundingClientRect();
            persistPosition(rect.left, rect.top);
        }

        dragState = null;
        document.body.classList.remove('react-chat-window-dragging');
    }

    function bindDragging() {
        var header = getHeader();
        if (!header) return;

        header.addEventListener('mousedown', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            startDrag(event.clientX, event.clientY);
            event.preventDefault();
        });

        header.addEventListener('touchstart', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            if (!event.touches || event.touches.length === 0) return;
            startDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mousemove', function (event) {
            if (!dragState) return;
            updateDrag(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!dragState || !event.touches || event.touches.length === 0) return;
            updateDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
    }

    function init() {
        var trigger = $('reactChatWindowButton');
        var closeButton = $('reactChatWindowCloseButton');
        var backdrop = $('react-chat-window-backdrop');

        if (trigger) {
            trigger.addEventListener('click', openWindow);
        }
        if (closeButton) {
            closeButton.addEventListener('click', closeWindow);
        }
        if (backdrop) {
            backdrop.addEventListener('click', closeWindow);
        }

        bindDragging();

        window.addEventListener('keydown', function (event) {
            var overlay = getOverlay();
            if (event.key === 'Escape' && overlay && !overlay.hidden) {
                closeWindow();
            }
        });

        window.addEventListener('resize', function () {
            var overlay = getOverlay();
            if (overlay && !overlay.hidden) {
                restorePosition();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.reactChatWindowHost = {
        ensureBundleLoaded: ensureBundleLoaded,
        openWindow: openWindow,
        closeWindow: closeWindow,
        isMounted: function () { return mounted; }
    };
})();
