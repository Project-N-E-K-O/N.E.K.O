/**
 * app-react-chat-window/minimize-and-idle-dock.js
 * Host-side controller for the exported React chat window.
 *
 * Contract copied from the original entrypoint:
 * - Dynamically loads the React bundle if needed
 * - Owns window open/close/minimize/drag state
 * - Owns chat view props + messages state
 * - Exposes a stable bridge for host code / IPC adapters
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.reactChatWindowHost = window.reactChatWindowHost || {};
    const I = window.__appReactChatWindowParts || (window.__appReactChatWindowParts = {});
    I.MINIMIZED_SIZE = 51;            // 桌面/手机：毛线球直径
    var ELECTRON_CHAT_MINIMIZED_FALLBACK_WINDOW_SIZE = 83; // chat.html BALL fallback: 51px visible ball + transparent carrier
    I.MINIMIZED_DOWN_OFFSET = 24;     // 放大后整体下移，更贴近猫 GIF
    I.isMinimizeTransitioning = false;
    var activeAnimationCleanup = null; // 当前进行中动画的清理函数
    I.pendingChatSurfaceMode = null;
    var pendingMinimizedSurfaceCommit = null;

    // ── Idle-dock: independent orchestration (Phase 4) ──────────
    // Positions the minimized ball next to CAT2/CAT3 return-ball.
    // Completely separated from setMinimized() — only reads minimized
    // state and calls setMinimized(true/false) externally when needed.

    I.isIdleDockTierActive = function isIdleDockTierActive() {
        return I.idleDockTier === I.IDLE_DOCK_TIER_CAT2 || I.idleDockTier === I.IDLE_DOCK_TIER_CAT3;
    }

    I.isGoodbyeIdleBallAppearanceActive = function isGoodbyeIdleBallAppearanceActive() {
        // 球形态下返回控件不是猫，最小化球不该去贴靠；外观状态由 app-ui 维护
        try {
            if (typeof window.getNekoGoodbyeIdleAppearance === 'function') {
                return window.getNekoGoodbyeIdleAppearance() === 'ball';
            }
        } catch (_) {}
        return window.__nekoGoodbyeIdleAppearance === 'ball';
    }

    I.readAutoGoodbyeVisualTier = function readAutoGoodbyeVisualTier() {
        try {
            if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function') {
                var state = window.nekoAutoGoodbye.getState();
                var tier = state && state.visualTier;
                return typeof tier === 'string' ? tier : I.IDLE_DOCK_TIER_NONE;
            }
        } catch (_) {}
        return I.IDLE_DOCK_TIER_NONE;
    }

    function getVisibleReturnButtonContainer() {
        if (I.isElectronChatWindow()) return null;
        return document.querySelector('[id$="-return-button-container"][data-neko-return-visible="true"]');
    }

    function getIdleDockTarget() {
        // The revived legacy full never docks its minimized orb to the idle cat —
        // it collapses in place and stays put (pre-deletion behavior). Compact's
        // CAT2/CAT3 return-cat dock is untouched.
        if (isLegacyFullMinimizedBall()) return null;
        if (!I.idleDockActive || !I.isIdleDockTierActive()) return null;
        var container = getVisibleReturnButtonContainer();
        if (!container || typeof container.getBoundingClientRect !== 'function') return null;
        var rect = container.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        var left = Math.round(rect.left - I.MINIMIZED_SIZE - I.HOME_IDLE_DOCK_GAP);
        var top = Math.round(rect.top + ((rect.height - I.MINIMIZED_SIZE) / 2) + I.MINIMIZED_DOWN_OFFSET);
        return {
            left: Math.max(0, Math.min(left, window.innerWidth - I.MINIMIZED_SIZE)),
            top: Math.max(0, Math.min(top, window.innerHeight - I.MINIMIZED_SIZE))
        };
    }

    function stopIdleDockMinimizeObserver() {
        if (I.idleDockMinimizeObserver) {
            try { I.idleDockMinimizeObserver.disconnect(); } catch (_) {}
            I.idleDockMinimizeObserver = null;
        }
    }

    I.clearIdleDockState = function clearIdleDockState() {
        stopIdleDockMinimizeObserver();
        I.idleDockActive = false;
        I.idleDockSavedPosition = null;
        I.idleDockTriggeredMinimize = false;
    }

    I.hasIdleDockPendingOrActive = function hasIdleDockPendingOrActive() {
        return !!(I.idleDockActive || I.idleDockTriggeredMinimize || I.idleDockMinimizeObserver);
    }

    function applyIdleDockPosition() {
        if (!I.minimized || I.isElectronChatWindow()) return;
        var shell = I.getShell();
        var target = getIdleDockTarget();
        if (!shell || !target) return;
        shell.style.left = target.left + 'px';
        shell.style.top = target.top + 'px';
        shell.classList.add('is-idle-docked');
    }

    function finishIdleDockMinimize(shell) {
        if (!shell || !I.isIdleDockTierActive() || I.idleDockActive) return;
        stopIdleDockMinimizeObserver();
        var rect = shell.getBoundingClientRect();
        I.idleDockSavedPosition = { left: rect.left, top: rect.top };
        I.idleDockActive = true;
        applyIdleDockPosition();
    }

    function scheduleIdleDockMinimizeFallback(shell) {
        if (!shell) return;
        window.setTimeout(function () {
            if (!I.idleDockTriggeredMinimize || I.idleDockActive || !I.isIdleDockTierActive()) return;
            var latestShell = I.getShell();
            if (!latestShell) return;
            if (I.isMinimizeTransitioning) {
                var pendingSurfaceMode = I.pendingChatSurfaceMode;
                var pendingSurfaceCommit = pendingMinimizedSurfaceCommit;
                cancelActiveAnimation();
                I.pendingChatSurfaceMode = pendingSurfaceMode;
                pendingMinimizedSurfaceCommit = pendingSurfaceCommit;
            }
            I.minimized = true;
            latestShell.classList.remove('is-collapsing', 'is-expanding');
            latestShell.style.transform = 'none';
            latestShell.style.removeProperty('width');
            latestShell.style.removeProperty('height');
            latestShell.style.removeProperty('right');
            latestShell.style.removeProperty('bottom');
            latestShell.classList.add('is-minimized');
            I.syncChatSurfaceModeUI();
            commitPendingMinimizedSurfaceMode();
            flushPendingChatSurfaceModeIfNeeded();
            if (!I.minimized || I.getCurrentChatSurfaceMode() !== 'minimized') {
                I.clearIdleDockState();
                return;
            }
            finishIdleDockMinimize(latestShell);
        }, 460);
    }

    function getElectronIdleDockBridge() {
        if (!I.isElectronChatWindow()) return null;
        var bridge = window.nekoChatWindow;
        if (!bridge || typeof bridge.getBounds !== 'function' || typeof bridge.setBounds !== 'function') {
            return null;
        }
        return bridge;
    }

    function normalizeElectronRect(rect) {
        if (!rect || typeof rect !== 'object') return null;
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        return { left: left, top: top, width: width, height: height };
    }

    function normalizeElectronWindowBoundsRect(bounds) {
        if (!bounds || typeof bounds !== 'object') return null;
        var left = Number.isFinite(Number(bounds.left)) ? Number(bounds.left) : Number(bounds.x);
        var top = Number.isFinite(Number(bounds.top)) ? Number(bounds.top) : Number(bounds.y);
        var width = Number(bounds.width);
        var height = Number(bounds.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        left = Math.round(left);
        top = Math.round(top);
        width = Math.round(width);
        height = Math.round(height);
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: left + width,
            bottom: top + height
        };
    }

    function rememberElectronIdleDockBounds(bounds) {
        var rect = normalizeElectronWindowBoundsRect(bounds);
        if (!rect) return null;
        I.electronIdleDockCurrentBounds = {
            x: rect.left,
            y: rect.top,
            width: rect.width,
            height: rect.height
        };
        return I.electronIdleDockCurrentBounds;
    }

    function isElectronChatWindowCollapsed(bridge) {
        if (!bridge || typeof bridge.isCollapsed !== 'function') return false;
        try {
            return !!bridge.isCollapsed();
        } catch (_) {
            return false;
        }
    }

    function getElectronChatMinimizedStateSignature(minimizedState, rect) {
        if (!minimizedState || !rect) return '0';
        return [
            '1',
            rect.left,
            rect.top,
            rect.width,
            rect.height
        ].join(':');
    }

    function getElectronChatMinimizedScreenRect(windowRect) {
        // GNOME Wayland 的 Chat 是工作区大小的透明载体；真实毛球位置由 preload 发布。
        var renderedBallRect = normalizeElectronWindowBoundsRect(
            window.__nekoMinimizedChatBallScreenRect
        );
        if (renderedBallRect) return renderedBallRect;

        if (!windowRect) return null;
        var left = Math.round(windowRect.left + Math.max(0, (windowRect.width - I.MINIMIZED_SIZE) / 2));
        var top = Math.round(windowRect.top + Math.max(0, (windowRect.height - I.MINIMIZED_SIZE) / 2));
        return {
            left: left,
            top: top,
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            right: left + I.MINIMIZED_SIZE,
            bottom: top + I.MINIMIZED_SIZE
        };
    }

    function dispatchElectronChatMinimizedState(reason) {
        if (!I.isElectronChatWindow()) return;
        var bridge = window.nekoChatWindow;
        if (!bridge || typeof bridge.getBounds !== 'function') return;

        var now = Date.now();
        var collapsed = isElectronChatWindowCollapsed(bridge);
        if (!collapsed) {
            if (I.electronChatMinimizedStateSignature === '0' &&
                reason === 'poll' &&
                now - I.electronChatMinimizedStatePublishedAt < I.ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS) {
                return;
            }
            I.electronChatMinimizedStateSignature = '0';
            I.electronChatMinimizedStatePublishedAt = now;
            window.dispatchEvent(new CustomEvent('neko:idle-chat-minimized-state', {
                detail: {
                    action: 'idle_chat_minimized_state',
                    source: 'chat-window',
                    reason: reason || 'sync',
                    minimized: false,
                    screenRect: null,
                    timestamp: now
                }
            }));
            return;
        }

        bridge.getBounds().then(function (bounds) {
            var windowRect = normalizeElectronWindowBoundsRect(bounds);
            if (!windowRect) return;
            var yarnRect = getElectronChatMinimizedScreenRect(windowRect);
            if (!yarnRect) return;
            var now = Date.now();
            var signature = getElectronChatMinimizedStateSignature(true, yarnRect);
            if (signature === I.electronChatMinimizedStateSignature &&
                reason === 'poll' &&
                now - I.electronChatMinimizedStatePublishedAt < I.ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS) {
                return;
            }
            I.electronChatMinimizedStateSignature = signature;
            I.electronChatMinimizedStatePublishedAt = now;
            window.dispatchEvent(new CustomEvent('neko:idle-chat-minimized-state', {
                detail: {
                    action: 'idle_chat_minimized_state',
                    source: 'chat-window',
                    reason: reason || 'sync',
                    minimized: true,
                    screenRect: yarnRect,
                    timestamp: now
                }
            }));
        }).catch(function () {});
    }

    function scheduleElectronChatMinimizedState(reason) {
        if (!I.isElectronChatWindow() || I.electronChatMinimizedStateFrame) return;
        I.electronChatMinimizedStateFrame = window.requestAnimationFrame(function () {
            I.electronChatMinimizedStateFrame = 0;
            dispatchElectronChatMinimizedState(reason || 'sync');
        });
    }

    I.ensureElectronChatMinimizedStateBridge = function ensureElectronChatMinimizedStateBridge() {
        if (!I.isElectronChatWindow() || I.electronChatMinimizedStateTimer) return;
        scheduleElectronChatMinimizedState('init');
        I.electronChatMinimizedStateTimer = window.setInterval(function () {
            scheduleElectronChatMinimizedState('poll');
        }, 500);
        window.addEventListener('resize', function () {
            scheduleElectronChatMinimizedState('resize');
        });
        window.addEventListener('mousemove', function () {
            scheduleElectronChatMinimizedState('pointer');
        }, { passive: true });
        window.addEventListener('mouseup', function () {
            scheduleElectronChatMinimizedState('pointer');
        }, { passive: true });
    }

    function clampElectronDockBounds(bounds, workArea) {
        if (!bounds) return null;
        var area = workArea && Number.isFinite(Number(workArea.x)) && Number.isFinite(Number(workArea.y))
            ? workArea
            : { x: 0, y: 0, width: window.screen && window.screen.availWidth || 0, height: window.screen && window.screen.availHeight || 0 };
        var maxX = Number(area.x) + Math.max(0, Number(area.width) - bounds.width);
        var maxY = Number(area.y) + Math.max(0, Number(area.height) - bounds.height);
        return {
            x: Math.round(Math.max(Number(area.x), Math.min(bounds.x, maxX))),
            y: Math.round(Math.max(Number(area.y), Math.min(bounds.y, maxY))),
            width: Math.round(bounds.width),
            height: Math.round(bounds.height)
        };
    }

    function electronVisibleYarnRectToWindowBounds(rect, carrierRect) {
        if (!rect || typeof rect !== 'object') return null;
        var normalized = normalizeElectronRect({
            left: Number.isFinite(Number(rect.left)) ? rect.left : rect.x,
            top: Number.isFinite(Number(rect.top)) ? rect.top : rect.y,
            width: rect.width,
            height: rect.height
        });
        if (!normalized) return null;
        var carrier = normalizeElectronWindowBoundsRect(carrierRect);
        var carrierWidth = carrier && carrier.width > normalized.width
            ? carrier.width
            : Math.max(ELECTRON_CHAT_MINIMIZED_FALLBACK_WINDOW_SIZE, Math.round(normalized.width));
        var carrierHeight = carrier && carrier.height > normalized.height
            ? carrier.height
            : Math.max(ELECTRON_CHAT_MINIMIZED_FALLBACK_WINDOW_SIZE, Math.round(normalized.height));
        var insetX = Math.max(0, (carrierWidth - normalized.width) / 2);
        var insetY = Math.max(0, (carrierHeight - normalized.height) / 2);
        return {
            x: Math.round(normalized.left - insetX),
            y: Math.round(normalized.top - insetY),
            width: Math.round(carrierWidth),
            height: Math.round(carrierHeight)
        };
    }

    async function applyElectronCat1PairMoveBounds(bounds, options) {
        var force = !!(options && options.force);
        var reason = options && typeof options.reason === 'string' && options.reason
            ? options.reason
            : 'cat1-pair-move';
        if (I.isElectronLinuxRuntime() && !force) return;
        var bridge = getElectronIdleDockBridge();
        if (!bridge || !isElectronChatWindowCollapsed(bridge)) return;
        if (I.hasElectronIdleDockPendingOrActive()) return;
        var carrierBounds = null;
        try {
            carrierBounds = await bridge.getBounds();
        } catch (_) {}
        var targetBounds = electronVisibleYarnRectToWindowBounds(bounds, carrierBounds);
        if (!targetBounds) return;
        try {
            if (typeof bridge.idleDockCommitCollapsedBounds === 'function') {
                await bridge.idleDockCommitCollapsedBounds(targetBounds);
            } else {
                bridge.setBounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height);
            }
            scheduleElectronChatMinimizedState(reason);
        } catch (_) {
            // A transient desktop move failure should not break the CAT1 animation loop.
        }
    }

    I.scheduleElectronCat1PairMoveBounds = function scheduleElectronCat1PairMoveBounds(bounds, options) {
        var force = !!(options && options.force);
        var reason = options && typeof options.reason === 'string' && options.reason
            ? options.reason
            : (options && typeof options.source === 'string' && options.source ? options.source : 'cat1-pair-move');
        if (!I.isElectronChatWindow()) return;
        if (I.isElectronLinuxRuntime() && !force) return;
        I.electronCat1PairMovePendingBounds = normalizeElectronRect({
            left: bounds && Number.isFinite(Number(bounds.left)) ? bounds.left : bounds && bounds.x,
            top: bounds && Number.isFinite(Number(bounds.top)) ? bounds.top : bounds && bounds.y,
            width: bounds && bounds.width,
            height: bounds && bounds.height
        });
        I.electronCat1PairMovePendingForce = I.electronCat1PairMovePendingForce || force;
        I.electronCat1PairMovePendingReason = reason;
        if (!I.electronCat1PairMovePendingBounds || I.electronCat1PairMoveBoundsFrame) return;
        I.electronCat1PairMoveBoundsFrame = window.requestAnimationFrame(function () {
            var pendingBounds = I.electronCat1PairMovePendingBounds;
            var pendingForce = I.electronCat1PairMovePendingForce;
            var pendingReason = I.electronCat1PairMovePendingReason || 'cat1-pair-move';
            I.electronCat1PairMovePendingBounds = null;
            I.electronCat1PairMovePendingForce = false;
            I.electronCat1PairMovePendingReason = '';
            I.electronCat1PairMoveBoundsFrame = 0;
            applyElectronCat1PairMoveBounds(pendingBounds, {
                force: pendingForce,
                reason: pendingReason
            });
        });
    }

    function isElectronIdleDockCurrent(generation) {
        return I.electronIdleDockDesired && generation === I.electronIdleDockGeneration;
    }

    function clearElectronIdleDockPositionFrame() {
        if (I.electronIdleDockPositionFrame) {
            window.cancelAnimationFrame(I.electronIdleDockPositionFrame);
            I.electronIdleDockPositionFrame = 0;
        }
    }

    function setElectronIdleDockTargetRect(targetRect) {
        I.electronIdleDockLastScreenRect = targetRect;
        I.electronIdleDockPositionSeq += 1;
    }

    function scheduleElectronIdleDockPosition() {
        if (!I.electronIdleDockActive || !I.electronIdleDockDesired || I.electronIdleDockPositionFrame) return;
        I.electronIdleDockPositionFrame = window.requestAnimationFrame(function () {
            I.electronIdleDockPositionFrame = 0;
            applyElectronIdleDockPosition();
        });
    }

    async function applyElectronIdleDockPosition() {
        var bridge = getElectronIdleDockBridge();
        var targetRect = normalizeElectronRect(I.electronIdleDockLastScreenRect);
        var positionSeq = I.electronIdleDockPositionSeq;
        if (!bridge || !targetRect || !I.electronIdleDockActive || !I.electronIdleDockDesired) return;

        var bounds = I.electronIdleDockCurrentBounds;
        if (!bounds) {
            try {
                bounds = rememberElectronIdleDockBounds(await bridge.getBounds());
            } catch (_) {
                bounds = null;
            }
            if (positionSeq !== I.electronIdleDockPositionSeq || !I.electronIdleDockActive || !I.electronIdleDockDesired) return;
        }
        if (!bounds || !Number.isFinite(Number(bounds.width)) || !Number.isFinite(Number(bounds.height))) {
            return;
        }

        var width = Math.max(1, Math.round(Number(bounds.width)));
        var height = Math.max(1, Math.round(Number(bounds.height)));
        var nextBounds = {
            x: Math.round(targetRect.left - width - I.HOME_IDLE_DOCK_GAP),
            y: Math.round(targetRect.top + (targetRect.height - height) / 2),
            width: width,
            height: height
        };

        if (!I.electronIdleDockWorkArea && typeof bridge.getWorkArea === 'function') {
            try {
                I.electronIdleDockWorkArea = await bridge.getWorkArea();
            } catch (_) {
                I.electronIdleDockWorkArea = null;
            }
            if (positionSeq !== I.electronIdleDockPositionSeq || !I.electronIdleDockActive || !I.electronIdleDockDesired) return;
        }
        nextBounds = clampElectronDockBounds(nextBounds, I.electronIdleDockWorkArea);
        if (positionSeq !== I.electronIdleDockPositionSeq || !I.electronIdleDockActive || !I.electronIdleDockDesired) return;

        bridge.setBounds(nextBounds.x, nextBounds.y, nextBounds.width, nextBounds.height);
        rememberElectronIdleDockBounds(nextBounds);
    }

    function clearElectronIdleDockRetry() {
        if (I.electronIdleDockRetryTimer) {
            window.clearTimeout(I.electronIdleDockRetryTimer);
            I.electronIdleDockRetryTimer = 0;
        }
    }

    function scheduleElectronIdleDockRetry(generation) {
        if (I.electronIdleDockRetryTimer || !I.electronIdleDockLastScreenRect || !isElectronIdleDockCurrent(generation)) return;
        I.electronIdleDockRetryTimer = window.setTimeout(function () {
            I.electronIdleDockRetryTimer = 0;
            if (I.electronIdleDockLastScreenRect && !I.electronIdleDockActive && isElectronIdleDockCurrent(generation)) {
                enterElectronIdleDock(I.electronIdleDockLastScreenRect);
            }
        }, 120);
    }

    I.hasElectronIdleDockPendingOrActive = function hasElectronIdleDockPendingOrActive() {
        return I.electronIdleDockActive || I.electronIdleDockEntering || I.electronIdleDockDesired || I.electronIdleDockRetryTimer;
    }

    function shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier) {
        return !!(detail && detail.reason === 'viewport-resize' && !activeTier);
    }

    function waitElectronIdleDockCommitRetry(delayMs) {
        return new Promise(function (resolve) {
            setTimeout(resolve, Math.max(0, delayMs || 0));
        });
    }

    async function commitElectronIdleDockCollapsedBounds(bridge, bounds, generation) {
        if (!bridge || !bounds) return false;
        if (typeof bridge.idleDockCommitCollapsedBounds === 'function') {
            for (var attempt = 0; attempt < 4; attempt += 1) {
                var result = null;
                try {
                    result = await bridge.idleDockCommitCollapsedBounds(bounds);
                } catch (_) {
                    result = null;
                }
                if (generation !== I.electronIdleDockGeneration || I.electronIdleDockDesired) return false;
                if (result !== false && result !== null && result !== undefined) {
                    rememberElectronIdleDockBounds(result);
                    return true;
                }
                if (attempt >= 3) break;
                await waitElectronIdleDockCommitRetry(80);
                if (generation !== I.electronIdleDockGeneration || I.electronIdleDockDesired) return false;
            }
        }
        if (typeof bridge.setBounds === 'function') {
            bridge.setBounds(bounds.x, bounds.y, bounds.width, bounds.height);
            rememberElectronIdleDockBounds(bounds);
            return true;
        }
        return false;
    }

    async function enterElectronIdleDock(screenRect) {
        // full 独立窗口（Electron part B）完全避开 idle-dock —— 与 web 路径 enterIdleDock 的
        // full 守卫对偶：CAT2/CAT3 idle tier 不应把展开的 full 窗口自动折叠/贴猫。full 用旧版
        // 独立窗口折叠机制（preload 物理缩窗），不参与 idle-dock。
        if (I.getCurrentChatSurfaceMode() === 'full' || isLegacyFullMinimizedBall()) return;
        // 隐藏的聊天窗口不 idle-dock：full 激活时 compact 窗口被 .hide()（document.hidden=true）
        // 仍会收到 idle 事件，若不挡会在后台折叠并 spawn 出与当前可见 surface 无关的毛线球。
        // 正常 minimized 态用 dimReactChatForMinimize(setOpacity 0) 而非 hide，document.hidden
        // 仍为 false，故此守卫不影响 minimized 态的 idle-dock 跟随。
        if (typeof document !== 'undefined' && document.hidden) return;
        var bridge = getElectronIdleDockBridge();
        var targetRect = normalizeElectronRect(screenRect);
        if (!bridge || !targetRect) return;
        if (!I.electronIdleDockDesired) {
            I.electronIdleDockDesired = true;
            I.electronIdleDockGeneration += 1;
        }
        var generation = I.electronIdleDockGeneration;
        setElectronIdleDockTargetRect(targetRect);

        if (I.electronIdleDockActive) {
            scheduleElectronIdleDockPosition();
            return;
        }

        if (I.electronIdleDockEntering) {
            return;
        }

        I.electronIdleDockEntering = true;
        try {
            I.electronIdleDockSavedBounds = await bridge.getBounds();
        } catch (_) {
            I.electronIdleDockSavedBounds = null;
        }
        var entrySavedBounds = I.electronIdleDockSavedBounds;
        if (!isElectronIdleDockCurrent(generation)) {
            I.electronIdleDockEntering = false;
            return;
        }

        var alreadyCollapsed = false;
        try {
            alreadyCollapsed = typeof bridge.isCollapsed === 'function' && bridge.isCollapsed();
        } catch (_) {
            alreadyCollapsed = false;
        }
        var shouldCollapseForIdleDock = !alreadyCollapsed;
        if (alreadyCollapsed) {
            rememberElectronIdleDockBounds(entrySavedBounds);
        }
        if (!alreadyCollapsed && typeof bridge.idleDockCollapse !== 'function') {
            I.electronIdleDockEntering = false;
            scheduleElectronIdleDockRetry(generation);
            return;
        }

        if (shouldCollapseForIdleDock) {
            var collapsedResult = null;
            try {
                collapsedResult = await bridge.idleDockCollapse();
            } catch (_) {
                collapsedResult = null;
            }
            try {
                alreadyCollapsed = typeof bridge.isCollapsed === 'function' && bridge.isCollapsed();
            } catch (_) {
                alreadyCollapsed = false;
            }
            if (!isElectronIdleDockCurrent(generation)) {
                try {
                    if (collapsedResult && alreadyCollapsed && entrySavedBounds && typeof bridge.idleDockExpand === 'function') {
                        await bridge.idleDockExpand(entrySavedBounds);
                    }
                } catch (_) {
                    // Best effort rollback; the newer generation owns the next visible state.
                }
                I.electronIdleDockEntering = false;
                return;
            }
            if (!collapsedResult || !alreadyCollapsed) {
                I.electronIdleDockEntering = false;
                scheduleElectronIdleDockRetry(generation);
                return;
            }
            rememberElectronIdleDockBounds(collapsedResult);
        }

        if (!isElectronIdleDockCurrent(generation)) {
            I.electronIdleDockEntering = false;
            return;
        }
        if (!I.electronIdleDockWorkArea && typeof bridge.getWorkArea === 'function') {
            try {
                I.electronIdleDockWorkArea = await bridge.getWorkArea();
            } catch (_) {
                I.electronIdleDockWorkArea = null;
            }
        }
        if (!isElectronIdleDockCurrent(generation)) {
            I.electronIdleDockEntering = false;
            return;
        }
        I.electronIdleDockTriggeredCollapse = shouldCollapseForIdleDock;
        I.electronIdleDockActive = true;
        I.electronIdleDockEntering = false;
        clearElectronIdleDockRetry();
        scheduleElectronIdleDockPosition();
        scheduleElectronChatMinimizedState('idle-dock-enter');
    }

    I.exitElectronIdleDock = async function exitElectronIdleDock(options) {
        var preserveCurrentPosition = !!(options && options.preserveCurrentPosition);
        var preserveScreenRect = normalizeElectronRect(options && options.preserveScreenRect);
        var bridge = getElectronIdleDockBridge();
        var wasActive = I.electronIdleDockActive;
        var triggeredCollapse = I.electronIdleDockTriggeredCollapse;
        var savedBounds = I.electronIdleDockSavedBounds;
        var currentBounds = I.electronIdleDockCurrentBounds;
        var workArea = I.electronIdleDockWorkArea;

        I.electronIdleDockDesired = false;
        I.electronIdleDockGeneration += 1;
        var exitGeneration = I.electronIdleDockGeneration;
        I.electronIdleDockActive = false;
        I.electronIdleDockTriggeredCollapse = false;
        I.electronIdleDockSavedBounds = null;
        I.electronIdleDockLastScreenRect = null;
        I.electronIdleDockEntering = false;
        I.electronIdleDockCurrentBounds = null;
        I.electronIdleDockWorkArea = null;
        clearElectronIdleDockRetry();
        clearElectronIdleDockPositionFrame();
        I.electronIdleDockPositionSeq += 1;

        if (!bridge || !wasActive) return;

        if (preserveCurrentPosition) {
            var preserveBounds = null;
            if (preserveScreenRect) {
                var basisBounds = currentBounds || savedBounds;
                if (!basisBounds && typeof bridge.getBounds === 'function') {
                    try {
                        basisBounds = await bridge.getBounds();
                    } catch (_) {
                        basisBounds = null;
                    }
                }
                if (exitGeneration !== I.electronIdleDockGeneration || I.electronIdleDockDesired) return;
                if (basisBounds &&
                    Number.isFinite(Number(basisBounds.width)) &&
                    Number.isFinite(Number(basisBounds.height))) {
                    preserveBounds = {
                        x: Math.round(preserveScreenRect.left - Math.max(1, Math.round(Number(basisBounds.width))) - I.HOME_IDLE_DOCK_GAP),
                        y: Math.round(preserveScreenRect.top + (preserveScreenRect.height - Math.max(1, Math.round(Number(basisBounds.height)))) / 2),
                        width: Math.max(1, Math.round(Number(basisBounds.width))),
                        height: Math.max(1, Math.round(Number(basisBounds.height)))
                    };
                    if (!workArea && typeof bridge.getWorkArea === 'function') {
                        try {
                            workArea = await bridge.getWorkArea();
                        } catch (_) {
                            workArea = null;
                        }
                    }
                    if (exitGeneration !== I.electronIdleDockGeneration || I.electronIdleDockDesired) return;
                    preserveBounds = clampElectronDockBounds(preserveBounds, workArea);
                }
            }
            await commitElectronIdleDockCollapsedBounds(bridge, preserveBounds, exitGeneration);
            if (exitGeneration !== I.electronIdleDockGeneration || I.electronIdleDockDesired) return;
            scheduleElectronChatMinimizedState('idle-dock-exit-preserve');
            return;
        }

        if (!savedBounds) return;

        if (triggeredCollapse && typeof bridge.idleDockExpand === 'function') {
            try {
                await bridge.idleDockExpand(savedBounds);
                scheduleElectronChatMinimizedState('idle-dock-exit');
                return;
            } catch (_) {}
        }
        bridge.setBounds(savedBounds.x, savedBounds.y, savedBounds.width, savedBounds.height);
        scheduleElectronChatMinimizedState('idle-dock-exit');
    }

    I.handleElectronIdleReturnBallState = function handleElectronIdleReturnBallState(detail) {
        if (!I.isElectronChatWindow()) return;
        var tier = detail && detail.tier;
        var activeTier = tier === I.IDLE_DOCK_TIER_CAT2 || tier === I.IDLE_DOCK_TIER_CAT3;
        if (detail && detail.visible && activeTier && detail.screenRect) {
            if (!I.hasElectronIdleDockPendingOrActive()) {
                enterElectronIdleDock(detail.screenRect);
            }
            return;
        }
        if (I.hasElectronIdleDockPendingOrActive()) {
            if (shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier)) {
                return;
            }
            var shouldPreserveCurrentPosition = activeTier && detail && (
                detail.reason === 'return-ball-drag-demotion'
                || detail.reason === 'return-ball-drag-end'
            );
            I.exitElectronIdleDock({
                preserveCurrentPosition: shouldPreserveCurrentPosition,
                preserveScreenRect: shouldPreserveCurrentPosition ? detail.screenRect : null,
            });
        }
    }

    // Enter idle-dock: minimize if needed, then position next to return-ball.
    // Enters through chatSurfaceMode so compact/full/minimized state stays in sync.
    I.enterIdleDock = function enterIdleDock() {
        if (I.isElectronChatWindow()) return;
        // full 完全避开 idle-dock：CAT2/CAT3 视觉层级不应把展开的 full 窗口自动
        // 最小化成球。这个检查必须在触发最小化之前，而不只在算 dock 位置时
        // （getIdleDockTarget 的 isLegacyFullMinimizedBall 守卫只能阻止 dock 定位，
        // 阻止不了 setChatSurfaceMode('minimized') 本身）。
        if (I.getCurrentChatSurfaceMode() === 'full' || isLegacyFullMinimizedBall()) return;

        if (I.minimized) {
            // Already minimized — save current position and dock immediately.
            var shell = I.getShell();
            if (shell) {
                var rect = shell.getBoundingClientRect();
                I.idleDockSavedPosition = { left: rect.left, top: rect.top };
            }
            I.idleDockActive = true;
            I.idleDockTriggeredMinimize = false;
            applyIdleDockPosition();
        } else {
            // Not minimized — trigger normal minimize, observe for completion.
            I.idleDockTriggeredMinimize = true;
            stopIdleDockMinimizeObserver();
            var shell = I.getShell();
            if (!shell) return;

            I.idleDockMinimizeObserver = new MutationObserver(function () {
                if (shell.classList.contains('is-minimized') && !shell.classList.contains('is-collapsing')) {
                    finishIdleDockMinimize(shell);
                }
            });
            I.idleDockMinimizeObserver.observe(shell, { attributes: true, attributeFilter: ['class'] });

            I.setChatSurfaceMode('minimized');
            scheduleIdleDockMinimizeFallback(shell);
        }
    }

    // Exit idle-dock: restore position and un-minimize if idle-dock triggered it.
    I.exitIdleDock = function exitIdleDock(options) {
        var preserveCurrentPosition = !!(options && options.preserveCurrentPosition);
        var wasActive = I.idleDockActive;
        var triggered = I.idleDockTriggeredMinimize;
        var wasTransitioning = I.isMinimizeTransitioning;
        var saved = I.idleDockSavedPosition;
        var shell = I.getShell();

        I.clearIdleDockState();

        if (shell) {
            shell.classList.remove('is-idle-docked');
            if (wasActive && saved && !preserveCurrentPosition) {
                shell.style.left = saved.left + 'px';
                shell.style.top = saved.top + 'px';
            }
        }

        if (triggered && !wasActive && wasTransitioning) {
            cancelActiveAnimation();
            I.minimized = false;
            if (shell) {
                shell.classList.remove('is-minimized', 'is-collapsing', 'is-idle-docked');
                shell.style.transform = 'none';
                if (I.savedShellSize) {
                    if (I.savedShellSize.width) shell.style.width = I.savedShellSize.width;
                    if (I.savedShellSize.height) shell.style.height = I.savedShellSize.height;
                }
                if (I.savedShellPosition) {
                    shell.style.left = I.savedShellPosition.left + 'px';
                    shell.style.top = I.savedShellPosition.top + 'px';
                }
            }
            I.savedShellSize = null;
            I.savedShellPosition = null;
            I.state.chatSurfaceMode = I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode);
            I.renderWindow();
            syncMinimizeUI();
            I.syncChatSurfaceModeUI();
            return;
        }

        if (wasActive && triggered && I.minimized && preserveCurrentPosition) {
            I.syncChatSurfaceModeUI();
            return;
        }

        if (wasActive && triggered && I.minimized) {
            I.setChatSurfaceMode(I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode));
        }
    }

    // ── End idle-dock ────────────────────────────────────────────

    function getCompactMinimizeBallTargetRect() {
        var root = I.getRoot();
        if (!root || typeof root.querySelector !== 'function') return null;
        var button = root.querySelector('.compact-chat-minimize-ball');
        if (!button || typeof button.getBoundingClientRect !== 'function') return null;
        var buttonRect = I.normalizeCompactDomRect(button.getBoundingClientRect());
        if (!buttonRect) return null;
        return {
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            left: buttonRect.left + buttonRect.width / 2 - I.MINIMIZED_SIZE / 2,
            top: buttonRect.top + buttonRect.height / 2 - I.MINIMIZED_SIZE / 2
        };
    }

    I.rememberCompactMinimizeBallTargetAnchor = function rememberCompactMinimizeBallTargetAnchor() {
        I.compactMinimizeBallTargetAnchor = null;
        if (I.getCurrentChatSurfaceMode() !== 'compact') return null;
        I.compactMinimizeBallTargetAnchor = getCompactMinimizeBallTargetRect();
        return I.compactMinimizeBallTargetAnchor;
    }

    function consumeCompactMinimizeBallTargetAnchor() {
        var targetRect = I.normalizeCompactDomRect(I.compactMinimizeBallTargetAnchor);
        I.compactMinimizeBallTargetAnchor = null;
        return targetRect;
    }

    function getMinimizedTargetFromCompactAnchor(anchorRect) {
        anchorRect = I.normalizeCompactDomRect(anchorRect);
        if (!anchorRect) return null;
        return {
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            left: Math.max(
                0,
                Math.min(
                    Math.round(anchorRect.left),
                    window.innerWidth - I.MINIMIZED_SIZE
                )
            ),
            top: Math.max(
                0,
                Math.min(
                    Math.round(anchorRect.top),
                    window.innerHeight - I.MINIMIZED_SIZE
                )
            )
        };
    }

    // 返回最小化后 shell 应达到的像素几何。
    // compact 折叠时，优先锚定到 React frame 内的毛线球按钮中心；React 在切到 minimized
    // 后会卸载 compact surface，所以点击入口要先记录 target rect。拿不到 target rect 时才退回
    // shell 左下角 fallback。
    function getMinimizedTarget(rect) {
        var compactTarget = isLegacyFullMinimizedBall()
            ? null
            : getMinimizedTargetFromCompactAnchor(consumeCompactMinimizeBallTargetAnchor());
        if (compactTarget) return compactTarget;

        // Fallback: collapse to the surface's own bottom-left corner. This keeps
        // legacy full behavior and covers compact cases where the inline yarn
        // icon was not measurable.
        return {
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            left: Math.max(0, Math.min(rect.left, window.innerWidth - I.MINIMIZED_SIZE)),
            top: Math.max(0, Math.min(rect.bottom - I.MINIMIZED_SIZE, window.innerHeight - I.MINIMIZED_SIZE))
        };
    }

    function getExpandedTargetFromSavedState() {
        var shell = I.getShell();
        if (!shell) return null;
        if (I.isMobileWidth()) return null;

        var width = I.savedShellSize ? parseFloat(I.savedShellSize.width) : NaN;
        var height = I.savedShellSize ? parseFloat(I.savedShellSize.height) : NaN;
        if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
            var rect = shell.getBoundingClientRect();
            width = rect.width;
            height = rect.height;
        }

        if (I.getCurrentChatSurfaceMode() === 'compact') {
            var compactTarget = I.getCompactSurfaceTarget();
            if (compactTarget) {
                return {
                    width: width,
                    height: height,
                    left: compactTarget.left,
                    top: compactTarget.top
                };
            }
        }

        if (isLegacyFullMinimizedBall()) {
            // Legacy full expands FROM the ball: the orb may have been dragged
            // while minimized, so the inline expand logic recomputes the dialog
            // from ballLeft/ballBottom (球的左下角 = 对话框的左下角). Opt out of any
            // saved-window-position pin here by returning null. (#1506 三态重构前的
            // 老 full 行为。) Window-position memory across refresh is handled
            // separately by restoreFullPosition() on open.
            return null;
        }

        var expandedTargetPosition = I.savedExpandedShellPosition
            || I.getStoredPosition()
            || (I.savedShellPosition
                ? {
                    left: I.savedShellPosition.left,
                    top: I.savedShellPosition.top
                }
                : null);

        return {
            width: width,
            height: height,
            left: expandedTargetPosition ? expandedTargetPosition.left : 0,
            top: expandedTargetPosition ? expandedTargetPosition.top : 0
        };
    }

    function cancelActiveAnimation() {
        if (activeAnimationCleanup) {
            activeAnimationCleanup();
            activeAnimationCleanup = null;
        }
        I.isMinimizeTransitioning = false;
        // 取消进行中的折叠/展开时一并清掉挂起的「按下挤压→瞬时折叠」延时（closeWindow
        // 也走这里），避免该回调在窗口关闭/重开后幽灵触发 setChatSurfaceMode('minimized')。
        I.clearCompactMinimizePressTimer();
        I.pendingChatSurfaceMode = null;
        pendingMinimizedSurfaceCommit = null;
    }

    // 最小化球皮肤跟随可恢复形态；紧凑 Electron 宿主会把历史 full 规整回 compact。
    function isLegacyFullMinimizedBall() {
        return I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode) === 'full';
    }

    I.applyMinimizedBallSkin = function applyMinimizedBallSkin(ballIcon) {
        var legacyFull = isLegacyFullMinimizedBall();
        if (ballIcon) {
            ballIcon.src = legacyFull
                ? I.CHAT_MINIMIZED_BALL_LEGACY_FULL_ICON_SRC
                : I.CHAT_MINIMIZED_BALL_ICON_SRC;
            ballIcon.srcset = legacyFull ? '' : I.CHAT_MINIMIZED_BALL_ICON_SRCSET;
        }
        var shell = I.getShell();
        if (shell) {
            // Glow only renders together with .is-minimized (see
            // full-chat-minimize.css); harmless while full is expanded.
            shell.classList.toggle('is-legacy-full-ball', legacyFull);
        }
    }

    function ensureMinimizedBallIcon() {
        var shell = I.getShell();
        if (!shell) return null;
        var icon = shell.querySelector('.react-chat-minimized-icon');
        if (!icon) {
            icon = document.createElement('img');
            icon.className = 'react-chat-minimized-icon';
            var legacyFullIcon = isLegacyFullMinimizedBall();
            icon.src = legacyFullIcon
                ? I.CHAT_MINIMIZED_BALL_LEGACY_FULL_ICON_SRC
                : I.CHAT_MINIMIZED_BALL_ICON_SRC;
            icon.srcset = legacyFullIcon ? '' : I.CHAT_MINIMIZED_BALL_ICON_SRCSET;
            icon.alt = '';
            icon.draggable = false;
            var handle = I.getHeader();
            if (handle) {
                handle.appendChild(icon);
            } else {
                shell.appendChild(icon);
            }
        }
        return icon;
    }

    I.setCompactChatState = function setCompactChatState(nextCompactChatState) {
        var normalized = I.normalizeCompactChatState(nextCompactChatState);
        if (normalized === 'input' && (I.state.homeTutorialInputLocked || I.isHomeTutorialInteractionLocked())) {
            return I.getCurrentCompactChatState();
        }
        if (I.state.compactChatState === normalized) {
            return normalized;
        }
        I.state.compactChatState = normalized;
        I.renderWindow();
        I.syncChatSurfaceModeUI();
        I.dispatchHostEvent('compact-chat-state-change', {
            state: normalized
        });
        return normalized;
    }

    function nextTutorialChatRequestId(prefix) {
        I.tutorialChatRequestSeq += 1;
        return prefix + '-' + Date.now() + '-' + I.tutorialChatRequestSeq;
    }

    I.setAvatarToolMenuOpen = function setAvatarToolMenuOpen(open, reason) {
        I.setViewProps({
            avatarToolMenuOpenRequest: {
                id: nextTutorialChatRequestId('avatar-tool-menu'),
                open: open === true,
                reason: typeof reason === 'string' ? reason : ''
            }
        });
        return true;
    }

    I.setCompactToolFanOpen = function setCompactToolFanOpen(open, reason) {
        I.setViewProps({
            compactToolFanOpenRequest: {
                id: nextTutorialChatRequestId('compact-tool-fan'),
                open: open === true,
                reason: typeof reason === 'string' ? reason : ''
            }
        });
        return true;
    }

    function commitPendingMinimizedSurfaceMode() {
        if (!pendingMinimizedSurfaceCommit) return false;

        var commit = pendingMinimizedSurfaceCommit;
        pendingMinimizedSurfaceCommit = null;
        I.state.chatSurfaceMode = commit.mode;
        I.resetCompactChatState();
        I.renderWindow();
        I.syncChatSurfaceModeUI();
        I.dispatchHostEvent('chat-surface-mode-change', {
            mode: commit.mode,
            previousMode: commit.previousMode
        });
        return true;
    }

    I.setChatSurfaceMode = function setChatSurfaceMode(nextMode) {
        var normalized = I.coerceChatSurfaceModeForHost(nextMode);
        var previousMode = I.getCurrentChatSurfaceMode();
        var nextMinimized = normalized === 'minimized';
        var previousMinimized = previousMode === 'minimized';
        if (previousMode === normalized) {
            if (I.isMinimizeTransitioning) {
                I.pendingChatSurfaceMode = null;
            }
            I.syncChatSurfaceModeUI();
            return normalized;
        }

        if (I.isMinimizeTransitioning) {
            I.pendingChatSurfaceMode = normalized;
            return previousMode;
        }
        I.pendingChatSurfaceMode = null;

        // 任何真实的 surface mode 变更都取消挂起的「按下挤压→延时折叠」：否则 full→compact 之类
        // 快速跳变会让 280ms 内的陈旧折叠回调在 mode 又回到 compact 时误折叠刚恢复的表面（Codex P2，
        // 仅靠回调里 getCurrentChatSurfaceMode()==='compact' 守卫挡不住这种 hop）。timer 自身的
        // minimize 调用此时 timer 已=0，clear 为 no-op，不影响正常折叠。
        I.clearCompactMinimizePressTimer();

        if (!previousMinimized && nextMinimized && previousMode === 'compact') {
            I.rememberCompactMinimizeBallTargetAnchor();
        }

        if (!nextMinimized) {
            I.lastRestorableChatSurfaceMode = normalized;
        } else if (!previousMinimized) {
            I.lastRestorableChatSurfaceMode = previousMode;
        }

        I.resetCompactChatState();
        I.syncGoodbyeComposerHidden('chat-surface-mode-change', { localOnly: true });
        I.requestGoodbyeComposerHiddenState('chat-surface-mode-change');

        if (!previousMinimized && nextMinimized && previousMode === 'full' && !I.isElectronChatWindow() && I.getShell()) {
            pendingMinimizedSurfaceCommit = {
                mode: normalized,
                previousMode: previousMode
            };
            setMinimized(true);
            return normalized;
        }

        pendingMinimizedSurfaceCommit = null;
        I.state.chatSurfaceMode = normalized;
        I.persistChatSurfaceModePreference(normalized);
        if (normalized === 'compact') {
            I.seedCompactSurfaceAnchorForRender();
        }
        I.renderWindow();

        if (nextMinimized !== previousMinimized) {
            setMinimized(nextMinimized);
        } else {
            I.syncChatSurfaceModeUI();
        }

        I.dispatchHostEvent('chat-surface-mode-change', {
            mode: normalized,
            previousMode: previousMode
        });
        return normalized;
    }

    I._handleIdleCat1PlaygroundYarnRequest = function _handleIdleCat1PlaygroundYarnRequest(event) {
        if (I.getCurrentChatSurfaceMode() === 'minimized') return;
        I.setChatSurfaceMode('minimized');
    }

    I.cycleChatSurfaceMode = function cycleChatSurfaceMode() {
        return I.setChatSurfaceMode(I.getNextChatSurfaceMode(I.getCurrentChatSurfaceMode()));
    }

    function flushPendingChatSurfaceModeIfNeeded() {
        if (I.isMinimizeTransitioning || !I.pendingChatSurfaceMode) return;

        var targetMode = I.pendingChatSurfaceMode;
        I.pendingChatSurfaceMode = null;
        if (targetMode === I.getCurrentChatSurfaceMode()) {
            return;
        }
        I.setChatSurfaceMode(targetMode);
    }

    function setMinimized(nextMinimized) {
        var shell = I.getShell();
        if (!shell) return;

        var wasMinimized = I.minimized;
        var willMinimize = !!nextMinimized;
        if (wasMinimized === willMinimize) return;
        if (I.isMinimizeTransitioning) return; // 防止动画期间重复触发
        I.isMinimizeTransitioning = true;

        I.minimized = willMinimize;

        if (I.isElectronChatWindow()) {
            shell.classList.remove('is-collapsing', 'is-expanding', 'is-minimized');
            shell.style.removeProperty('transform');
            shell.style.removeProperty('transform-origin');
            shell.style.removeProperty('opacity');
            I.isMinimizeTransitioning = false;
            I.syncChatSurfaceModeUI();
            return;
        }

        if (willMinimize) {
            // ---- 折叠动画：向对话框左下角缩放 ----
            var rect = shell.getBoundingClientRect();

            // 1. 保存当前位置和尺寸，展开时用
            //    如果没有内联宽高（如 chat.html 全屏模式），
            //    使用计算后的像素值，确保展开时能正确恢复
            I.savedShellSize = {
                width: shell.style.width || (rect.width + 'px'),
                height: shell.style.height || (rect.height + 'px')
            };
            I.savedShellPosition = {
                left: rect.left,
                top: rect.top
            };

            // 1b. 锁定当前像素几何到内联样式，防止切类后尺寸跳变
            //     （chat.html 全屏规则退出后 shell 会回落到默认尺寸）
            shell.style.width = rect.width + 'px';
            shell.style.height = rect.height + 'px';
            shell.style.left = rect.left + 'px';
            shell.style.top = rect.top + 'px';

            // 2. 最小化后的目标几何：桌面=50px 圆球 / 手机=全宽底部胶囊
            var target = getMinimizedTarget(rect);
            var targetLeft = target.left;
            var targetTop = target.top;

            // 3. 计算缩放比，并反推 transform-origin，使缩放后的 shell
            //    视觉终点落在 target 上。fallback 的 shell 左下角目标会自然得到
            //    0px 100% 等价原点；compact 的毛线球槽位目标则不会再从左下角跳过去。
            var sx = rect.width > 0 ? target.width / rect.width : 1;
            var sy = rect.height > 0 ? target.height / rect.height : 1;
            var originX = 0;
            var originY = rect.height;
            var originDenomX = 1 - sx;
            var originDenomY = 1 - sy;
            if (Math.abs(originDenomX) > 0.0001) {
                originX = (targetLeft - rect.left) / originDenomX;
            }
            if (Math.abs(originDenomY) > 0.0001) {
                originY = (targetTop - rect.top) / originDenomY;
            }
            if (!Number.isFinite(originX)) originX = 0;
            if (!Number.isFinite(originY)) originY = rect.height;

            // 4. 初始 transform = identity，添加过渡类
            shell.style.transform = 'scale(1, 1)';
            shell.style.transformOrigin = originX + 'px ' + originY + 'px';
            shell.classList.add('is-collapsing');
            void shell.offsetHeight; // 强制 reflow

            var handled = false;
            var collapseTimer = null;
            var collapseScaleFrame = 0;
            var collapseScaleInnerFrame = 0;
            function cancelCollapseScaleFrames() {
                if (collapseScaleFrame) {
                    window.cancelAnimationFrame(collapseScaleFrame);
                    collapseScaleFrame = 0;
                }
                if (collapseScaleInnerFrame) {
                    window.cancelAnimationFrame(collapseScaleInnerFrame);
                    collapseScaleInnerFrame = 0;
                }
            }

            // 5. 设置目标 transform，触发动画
            //    动画期间 left/top 不动，只通过动态 origin 缩放到 target。
            collapseScaleFrame = requestAnimationFrame(function () {
                collapseScaleFrame = 0;
                if (handled || !shell.classList.contains('is-collapsing') || shell.classList.contains('is-minimized')) return;
                collapseScaleInnerFrame = requestAnimationFrame(function () {
                    collapseScaleInnerFrame = 0;
                    if (handled || !shell.classList.contains('is-collapsing') || shell.classList.contains('is-minimized')) return;
                    shell.style.transform = 'scale(' + sx + ', ' + sy + ')';
                });
            });

            // 6. 过渡结束后切换到最终的 minimized 状态
            var finishCollapse = function () {
                if (handled) return;
                handled = true;
                cancelCollapseScaleFrames();
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                shell.style.removeProperty('transform-origin');
                // 清除内联尺寸，让 .is-minimized 的 CSS 生效
                shell.style.removeProperty('width');
                shell.style.removeProperty('height');
                shell.classList.remove('is-mobile-content-capped');
                shell.style.removeProperty('right');
                shell.style.removeProperty('bottom');
                // 将位置设为对话框左下角
                shell.style.left = targetLeft + 'px';
                shell.style.top = targetTop + 'px';
                shell.classList.add('is-minimized');
                // The ball icon is created once at init (when the restorable
                // surface is usually compact); re-apply the skin now so a
                // minimize from the revived legacy full shows its breathing-light
                // orb instead of the stale compact yarn ball.
                I.applyMinimizedBallSkin(ensureMinimizedBallIcon());
                commitPendingMinimizedSurfaceMode();
                I.isMinimizeTransitioning = false;
                flushPendingChatSurfaceModeIfNeeded();
            };
            var onEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishCollapse();
            };
            shell.addEventListener('transitionend', onEnd);
            collapseTimer = setTimeout(finishCollapse, 420); // 兜底

            // 注册清理句柄，供 closeWindow / 下次动画调用
            activeAnimationCleanup = function () {
                cancelCollapseScaleFrames();
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                shell.style.removeProperty('transform-origin');
                pendingMinimizedSurfaceCommit = null;
                handled = true;
            };

        } else {
            // ---- 展开动画：从最小化态（桌面圆球 / 手机底部胶囊）展开 ----
            var curRect = shell.getBoundingClientRect();
            var ballLeft = curRect.left;
            var collapsedTop = curRect.top;
            // 桌面圆球的 height≈50，手机胶囊的 height≈48；curRect 直接反映真实值
            var ballBottom = curRect.top + (curRect.height || I.MINIMIZED_SIZE);

            // 恢复保存的尺寸
            var previousSetupVisibility = shell.style.visibility;
            var setupVisibilityHidden = true;
            var restoreSetupVisibility = function () {
                if (!setupVisibilityHidden) return;
                setupVisibilityHidden = false;
                if (previousSetupVisibility) {
                    shell.style.visibility = previousSetupVisibility;
                } else {
                    shell.style.removeProperty('visibility');
                }
            };
            // Removing .is-minimized makes the full React surface visible again.
            // Keep it hidden while we restore/measure geometry so the browser
            // cannot paint one frame at the measurement position before the
            // scale-from-ball transform is ready.
            shell.style.visibility = 'hidden';
            shell.classList.remove('is-minimized');
            shell.style.removeProperty('right');
            shell.style.removeProperty('bottom');
            if (I.isMobileWidth()) {
                // 手机端：宽度由 CSS calc(100vw - 12px) 控制，清除内联宽度
                shell.style.removeProperty('width');
                // 高度：优先使用用户手动设置的高度，否则自动计算上限 85vh
                var mobileMaxH = I.getMobileMaxHeight();
                var savedHeightPx = I.savedShellSize ? parseFloat(I.savedShellSize.height) : NaN;
                var restoreHeight;
                if (I.mobileUserHeight > 0) {
                    restoreHeight = Math.min(I.mobileUserHeight, mobileMaxH);
                } else if (isFinite(savedHeightPx) && savedHeightPx > 0) {
                    restoreHeight = Math.min(savedHeightPx, mobileMaxH);
                } else {
                    restoreHeight = mobileMaxH;
                }
                if (restoreHeight > 0) shell.style.height = restoreHeight + 'px';
            } else if (I.savedShellSize) {
                if (I.savedShellSize.width) shell.style.width = I.savedShellSize.width;
                if (I.savedShellSize.height) shell.style.height = I.savedShellSize.height;
            }

            // 以球的位置为展开后对话框的左下角来计算展开位置
            // 先落到最终目标位置获取真实尺寸，避免 (0,0) 测量态被绘制出来。
            var expandedTarget = getExpandedTargetFromSavedState();
            if (expandedTarget) {
                shell.style.left = expandedTarget.left + 'px';
                shell.style.top = expandedTarget.top + 'px';
            }
            shell.style.transform = 'none';
            void shell.offsetHeight;
            var expandedRect = shell.getBoundingClientRect();

            // 尺寸无效时（overlay 仍隐藏等边界情况）跳过动画，直接恢复
            if (!expandedRect.width || !expandedRect.height) {
                shell.style.transform = 'none';
                // 尝试恢复到保存的位置
                if (I.savedShellPosition) {
                    shell.style.left = I.savedShellPosition.left + 'px';
                    shell.style.top = I.savedShellPosition.top + 'px';
                } else if (!I.isMobileWidth()) {
                    I.restorePosition();
                }
                I.savedShellSize = null;
                I.savedShellPosition = null;
                I.isMinimizeTransitioning = false;
                flushPendingChatSurfaceModeIfNeeded();
                restoreSetupVisibility();
                requestAnimationFrame(function () {
                    var r = shell.getBoundingClientRect();
                    var clamped = I.clampPosition(r.left, r.top);
                    if (clamped.left !== r.left || clamped.top !== r.top) {
                        I.applyPosition(clamped.left, clamped.top);
                    }
                });
            } else {

            // 球的左下角 = 展开后对话框的左下角
            var expandedLeft = ballLeft;
            var expandedTop = ballBottom - expandedRect.height;
            if (expandedTarget) {
                expandedLeft = expandedTarget.left;
                expandedTop = expandedTarget.top;
            }

            // 先不 clamp，让动画从球位置自然展开，动画结束后再 clamp
            shell.style.left = ballLeft + 'px';
            shell.style.top = collapsedTop + 'px';
            shell.style.transform = 'none';
            void shell.offsetHeight;

            // 重新获取展开后的真实 rect（位置可能已改变）
            expandedRect = shell.getBoundingClientRect();

            // 计算初始缩放：transform-origin 为左下角 (0% 100%)
            // 从当前最小化态的真实尺寸缩回（桌面 50x50 / 手机 full-width x 48），
            // 视觉上的左下角保持不变。
            var sx2 = curRect.width > 0 ? curRect.width / expandedRect.width : 1;
            var sy2 = curRect.height > 0 ? curRect.height / expandedRect.height : 1;

            // 设置初始 transform（看起来还是左下角的小圆）
            shell.style.transform = 'scale(' + sx2 + ', ' + sy2 + ')';
            shell.classList.add('is-expanding');
            void shell.offsetHeight; // 强制 reflow
            restoreSetupVisibility();

            // 动画到 identity（展开到完整尺寸）
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    shell.style.left = expandedLeft + 'px';
                    shell.style.top = expandedTop + 'px';
                    shell.style.transform = 'scale(1, 1)';
                });
            });

            // 动画结束后清理
            var expandHandled = false;
            var expandTimer = null;
            var finishExpand = function () {
                if (expandHandled) return;
                expandHandled = true;
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                var surfaceModeAfterExpand = I.getCurrentChatSurfaceMode();
                I.savedShellSize = null;
                I.savedShellPosition = null;
                I.isMinimizeTransitioning = false;
                flushPendingChatSurfaceModeIfNeeded();
                I.scheduleMobileContentLayout();
                // 确保位置不溢出；全屏模式（/chat）不持久化，
                // 否则 (0,0) 会覆盖 index.html 中用户保存的窗口位置
                requestAnimationFrame(function () {
                    if (I.isMobileWidth()) {
                        I.restorePosition();
                        return;
                    }
                    if (surfaceModeAfterExpand === 'minimized') {
                        return;
                    }
                    if (surfaceModeAfterExpand === 'full') {
                        // full 从球展开后 expandedTop = ballBottom - height，球被拖到
                        // 视口顶部时这会是负值，full 大半跑出屏幕、标题栏够不着。
                        // syncCompactSurfaceAnchor 只管 compact，对 full 是 no-op，
                        // 所以这里显式 clamp 进视口（clampPosition 保留标题栏可达）。
                        var fullShell = I.getShell();
                        if (fullShell) {
                            var fullRect = fullShell.getBoundingClientRect();
                            var clampedFull = I.clampPosition(fullRect.left, fullRect.top);
                            if (clampedFull.left !== fullRect.left || clampedFull.top !== fullRect.top) {
                                I.applyPosition(clampedFull.left, clampedFull.top);
                            }
                        }
                        return;
                    }
                    I.syncCompactSurfaceAnchor();
                });
            };
            var onExpandEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishExpand();
            };
            shell.addEventListener('transitionend', onExpandEnd);
            expandTimer = setTimeout(finishExpand, 420); // 兜底

            // 注册清理句柄
            activeAnimationCleanup = function () {
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                restoreSetupVisibility();
                expandHandled = true;
            };

            } // end of else (valid dimensions)
        }

        // 更新按钮图标和 aria
        I.syncChatSurfaceModeUI();
    }

    function syncMinimizeUI() {
        var button = I.getMinimizeButton();
        var btnIcon = I.getMinimizeIcon();
        var ballIcon = ensureMinimizedBallIcon();
        if (button) {
            button.setAttribute('aria-label', I.minimized ? I.getI18nText('chat.reactWindowRestore', '恢复聊天框') : I.getI18nText('chat.reactWindowMinimize', '最小化聊天框'));
            button.title = I.minimized ? I.getI18nText('chat.reactWindowRestoreShort', '恢复') : I.getI18nText('chat.reactWindowMinimizeShort', '最小化');
        }
        if (btnIcon) {
            btnIcon.src = I.minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = I.minimized ? I.getI18nText('chat.reactWindowRestore', '恢复聊天框') : I.getI18nText('chat.reactWindowMinimize', '最小化聊天框');
        }
        // 重置悬浮球图标到默认态（清除可能残留的 hover 图标），并按 restorable 形态
        // 选皮肤：compact=毛线球 / full=呼吸灯旧球。
        I.applyMinimizedBallSkin(ballIcon);
    }

    I.syncChatSurfaceModeUI = function syncChatSurfaceModeUI() {
        var shell = I.getShell();
        var button = I.getMinimizeButton();
        var btnIcon = I.getMinimizeIcon();
        var ballIcon = ensureMinimizedBallIcon();
        var surfaceMode = I.getCurrentChatSurfaceMode();
        // full 与 compact 点头部按钮的真实动作都是「最小化」（getNextChatSurfaceMode
        // 对二者都返回 'minimized'），所以 full 态也用最小化文案，别再写「切换到紧凑」。
        var ariaLabel = surfaceMode === 'minimized'
            ? I.getI18nText('chat.reactWindowRestore', '恢复聊天框')
            : I.getI18nText('chat.reactWindowMinimize', '最小化聊天框');
        var shortLabel = surfaceMode === 'minimized'
            ? I.getI18nText('chat.reactWindowRestoreShort', '恢复')
            : I.getI18nText('chat.reactWindowMinimizeShort', '最小化');
        if (button) {
            button.setAttribute('aria-label', ariaLabel);
            button.title = shortLabel;
        }
        if (btnIcon) {
            btnIcon.src = I.minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = ariaLabel;
        }
        I.applyMinimizedBallSkin(ballIcon);
        if (shell) {
            shell.setAttribute('data-chat-surface-mode', surfaceMode);
            shell.setAttribute('data-compact-chat-state', I.getCurrentCompactChatState());
        }
        I.scheduleCompactMinimizeBallTracking();
    }

    I.toggleMinimized = function toggleMinimized() {
        if (I.isMinimizeTransitioning) return;
        if (I.minimized && I.idleDockActive && I.idleDockSavedPosition) {
            var shell = I.getShell();
            if (shell) {
                shell.style.left = I.idleDockSavedPosition.left + 'px';
                shell.style.top = I.idleDockSavedPosition.top + 'px';
                shell.classList.remove('is-idle-docked');
            }
            I.idleDockActive = false;
            I.idleDockSavedPosition = null;
            I.idleDockTier = I.IDLE_DOCK_TIER_NONE;
            stopIdleDockMinimizeObserver();
        }
        I.cycleChatSurfaceMode();
    }

    I.prewarmUserDisplayName = function prewarmUserDisplayName() {
        if (!window.appChat || typeof window.appChat.ensureUserDisplayName !== 'function') return;
        Promise.resolve(window.appChat.ensureUserDisplayName()).catch(function (error) {
            console.warn('[ReactChatWindow] preload user display name failed:', error);
        });
    }

    function isMainUIHiddenByModelManager() {
        try {
            if (typeof window.isMainUIHiddenByModelManager === 'function') {
                return window.isMainUIHiddenByModelManager();
            }
        } catch (_) {}
        return !!(document.body && document.body.classList.contains('neko-main-ui-hidden-by-model-manager'));
    }

    var pendingOpenAfterModelManagerHidden = false;

    I.openWindow = function openWindow() {
        if (!I.isElectronChatWindow() && isMainUIHiddenByModelManager()) {
            pendingOpenAfterModelManagerHidden = true;
            return;
        }
        pendingOpenAfterModelManagerHidden = false;

        var overlay = I.getOverlay();
        if (!overlay) return;

        I.prewarmUserDisplayName();
        I.ensureBundleLoaded()
            .then(function () {
                if (!I.isElectronChatWindow() && isMainUIHiddenByModelManager()) {
                    pendingOpenAfterModelManagerHidden = true;
                    return;
                }
                // closeWindow 已经会重置 minimized，所以到这里通常 minimized=false
                // 但如果外部直接调用 openWindow（未经 closeWindow），仍需处理
                var wasMinimized = I.minimized;
                if (wasMinimized) {
                    // Opening a minimized window restores the last real surface.
                    // Reset the logical surface BEFORE mountWindow() so React
                    // rebuilds the compact body instead of the (blank) minimized
                    // surface; closeWindow performs the same reset when it clears
                    // the minimized shell.
                    I.state.chatSurfaceMode = I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode);
                    I.resetCompactChatState();
                }
                if (I.getCurrentChatSurfaceMode() === 'compact') {
                    I.seedCompactSurfaceAnchorForRender();
                }
                if (!I.mountWindow()) {
                    I.showToast(I.getI18nText('chat.reactWindowMountFailed', '聊天框挂载失败'), 3000);
                    return;
                }
                if (wasMinimized) {
                    // overlay 可能还隐藏，先显示再做展开动画
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    setMinimized(false);
                    I.scheduleMobileContentLayout();
                } else {
                    if (I.shouldDelayCompactSurfaceOpenForModel()) {
                        I.compactSurfacePendingModelOpen = true;
                        overlay.hidden = true;
                        document.body.classList.remove('react-chat-window-open');
                        return;
                    }
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    if (I.getCurrentChatSurfaceMode() === 'compact') {
                        I.syncCompactSurfaceAnchor();
                        I.scheduleCompactMinimizeBallTracking();
                    } else if (I.getCurrentChatSurfaceMode() === 'full') {
                        // 刷新/打开时还原 full 记住的位置（无记忆则保持居中）。
                        I.restoreFullPosition();
                    }
                    I.scheduleMobileContentLayout();
                }
                // closeWindow / hidden-state turn-end both invalidate the
                // GalGame option list, so reopening must re-fetch for the
                // latest assistant turn or the user would see a permanently
                // empty panel until the next reply arrives.
                // Wait for app-chat-adapter's realistic queue to drain before
                // building the request — same race the turn-end handler
                // protects against, just with a shorter cap because by the
                // time the user reopens the window the queue has usually
                // already finished.
                if (I.state.galgameModeEnabled) {
                    var seqAtOpen = I.state._galgameRequestSeq;
                    I.waitForAssistantBubblesFlushed(2000).then(function () {
                        if (!I.state.galgameModeEnabled) return;
                        if (I.state._galgameRequestSeq !== seqAtOpen) return;
                        var overlayNow = I.getOverlay();
                        if (!overlayNow || overlayNow.hidden) return;
                        I.fetchGalgameOptionsForLatestTurn();
                    });
                }
            })
            .catch(function (error) {
                console.error('[ReactChatWindow] open failed:', error);
                I.showToast(I.getI18nText('chat.reactWindowLoadFailed', '聊天框资源加载失败'), 3500);
            });
    }

    I.closeWindow = function closeWindow() {
        var overlay = I.getOverlay();
        if (!overlay) return;
        pendingOpenAfterModelManagerHidden = false;
        // Closing the overlay should also abort any in-flight GalGame fetch
        // (parity with setGalgameModeEnabled(false) / setMessages /
        // clearMessages). Without this, a request that lands after close
        // still passes the seq guard and writes options into hidden state,
        // surfacing stale A/B/C the next time the user opens the window.
        I.invalidatePendingGalgameRequest();
        cancelActiveAnimation(); // 清理进行中的折叠/展开回调
        I.pendingChatSurfaceMode = null;
        I.clearIdleDockState();
        I.deactivateAvatarTool();
        I.hideIdleCat1CompactMirror('close-window');

        // 如果当前处于最小化状态，恢复 shell 到正常态
        if (I.minimized) {
            var shell = I.getShell();
            if (shell) {
                shell.classList.remove('is-minimized');
                if (I.savedShellSize) {
                    if (I.savedShellSize.width) shell.style.width = I.savedShellSize.width;
                    if (I.savedShellSize.height) shell.style.height = I.savedShellSize.height;
                }
                if (I.savedShellPosition) {
                    shell.style.left = I.savedShellPosition.left + 'px';
                    shell.style.top = I.savedShellPosition.top + 'px';
                }
                shell.style.removeProperty('right');
                shell.style.removeProperty('bottom');
                shell.style.transform = 'none';
            }
            I.minimized = false;
            // closeWindow clears the minimized shell directly without routing
            // through setChatSurfaceMode, so the logical surface must be reset
            // too. Otherwise state.chatSurfaceMode stays 'minimized' and the next
            // openWindow() rebuilds the React props with chatSurfaceMode:
            // 'minimized', rendering a blank body over a no-longer-minimized
            // shell.
            I.state.chatSurfaceMode = I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode);
            I.resetCompactChatState();
            I.savedShellSize = null;
            I.savedShellPosition = null;
            I.syncChatSurfaceModeUI();
        }

        overlay.hidden = true;
        I.resetCompactChatState();
        document.body.classList.remove('react-chat-window-open');
        I.stopCompactMinimizeBallTracking();
        I.clearMobileContentCap();
        I.handleAvatarToolStateChange({
            active: false,
            toolId: null,
            tool: null,
            timestamp: Date.now()
        });
    }

    window.addEventListener('neko:main-ui-hidden-by-model-manager-changed', function(event) {
        if (I.isElectronChatWindow()) return;
        var hidden = !!(event && event.detail && event.detail.hidden);
        if (hidden || !pendingOpenAfterModelManagerHidden) return;
        pendingOpenAfterModelManagerHidden = false;
        I.openWindow();
    });

})();
