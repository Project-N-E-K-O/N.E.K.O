/**
 * app-interpage/guide-targets.js
 * Inter-page / cross-tab communication.
 *
 * Handles BroadcastChannel dispatch, postMessage listeners, model hot-reload, UI commands, and overlay cleanup.
 * Dependencies loaded before these parts:
 * - app-state.js -> window.appState, window.appConst
 * Runtime dependencies available by the time handlers fire:
 * - window.showStatusToast
 * - window.stopMicCapture / window.clearAudioQueue
 * - window.live2dManager / window.vrmManager
 * - initLive2DModel / initVRMModel globals
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appInterpage = window.appInterpage || {};
    const I = window.__appInterpageParts || (window.__appInterpageParts = {});
    function isYuiGuidePcCursorOnlyMode() {
        return I.isYuiGuidePcOverlayAvailable();
    }

    function createYuiGuideTargetGeometryRegistry() {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialTargetGeometryRegistry === 'function'
        ) {
            return window.YuiGuideCommon.createTutorialTargetGeometryRegistry();
        }
        return null;
    }

    function getYuiGuideTargetGeometryRegistry() {
        if (!I.yuiGuideTargetGeometryRegistry) {
            I.yuiGuideTargetGeometryRegistry = createYuiGuideTargetGeometryRegistry();
        }
        return I.yuiGuideTargetGeometryRegistry;
    }

    function getYuiGuideChatTargetRegistryEntryByExternalKind(kind) {
        var registry = getYuiGuideTargetGeometryRegistry();
        if (!registry || typeof registry.getByExternalKind !== 'function') {
            return null;
        }
        return registry.getByExternalKind(kind);
    }

    I.getYuiGuideChatTargetShape = function getYuiGuideChatTargetShape(kind) {
        if (
            kind === 'avatar-tools'
            || kind === 'galgame'
            || kind === 'tool-toggle'
            || kind === 'avatar-tool-items'
            || kind === 'avatar-tools-and-items'
            || kind === 'mini-game-choices'
        ) {
            return 'circle';
        }
        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        return entry && entry.shape ? entry.shape : 'rounded-rect';
    }

    function shouldAlignYuiGuideChatSpotlightToCapsuleText(kind, variant) {
        return kind === 'input' && variant === 'plain-capsule';
    }

    var YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO = 0.6;

    function getYuiGuideChatSpotlightTarget(kind) {
        if (!kind || typeof document === 'undefined') {
            return null;
        }

        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        if (entry && Array.isArray(entry.localSelectors)) {
            var registryTarget = null;
            entry.localSelectors.some(function (selector) {
                registryTarget = getYuiGuideChatVisibleElement(selector);
                return !!registryTarget;
            });
            if (registryTarget) {
                return registryTarget;
            }
        }

        if (kind === 'input') {
            return document.querySelector('#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]')
                || document.querySelector('#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]')
                || document.querySelector('#react-chat-window-root .compact-chat-surface-frame')
                || document.querySelector('#react-chat-window-root .compact-chat-surface-shell')
                || document.querySelector('#react-chat-window-root .composer-panel')
                || document.querySelector('#react-chat-window-root .composer-input-shell')
                || document.getElementById('text-input-area');
        }

        if (kind === 'window') {
            return document.getElementById('react-chat-window-shell');
        }

        var itemTargets = getYuiGuideChatSpotlightItemTargets(kind);
        if (itemTargets.length > 0) {
            return itemTargets[0];
        }

        return null;
    }

    function getYuiGuideChatCapsuleTextAnchor() {
        var anchor = getYuiGuideChatVisibleElement('#react-chat-window-root [data-compact-hit-region-id="capsule:text"]')
            || getYuiGuideChatVisibleElement('#react-chat-window-root .compact-chat-capsule-button');
        if (!anchor || typeof anchor.getBoundingClientRect !== 'function') {
            return null;
        }
        var rect = anchor.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        return {
            element: anchor,
            rect: rect
        };
    }

    function getYuiGuideChatSpotlightSourceRect(kind, variant, rect) {
        var sourceRect = {
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height
        };
        if (shouldAlignYuiGuideChatSpotlightToCapsuleText(kind, variant)) {
            var anchor = getYuiGuideChatCapsuleTextAnchor();
            var anchorRect = anchor && anchor.rect;
            if (
                anchorRect
                && anchorRect.left > rect.left
                && anchorRect.left < rect.left + rect.width
            ) {
                var anchorOffsetX = anchorRect.left - rect.left;
                sourceRect.left = rect.left + anchorOffsetX * YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO;
            }
        }
        return { rect: sourceRect };
    }

    function getYuiGuideChatVisibleElement(selector, root) {
        var scope = root || document;
        var elements = Array.prototype.slice.call(scope.querySelectorAll(selector));
        for (var index = 0; index < elements.length; index += 1) {
            var element = elements[index];
            var rect = element && typeof element.getBoundingClientRect === 'function'
                ? element.getBoundingClientRect()
                : null;
            if (rect && rect.width > 0 && rect.height > 0) {
                return element;
            }
        }
        return null;
    }

    function getYuiGuideChatVisibleElements(selector, root) {
        var scope = root || document;
        return Array.prototype.slice.call(scope.querySelectorAll(selector)).filter(function (element, index, array) {
            var rect = element && typeof element.getBoundingClientRect === 'function'
                ? element.getBoundingClientRect()
                : null;
            return element && array.indexOf(element) === index && rect && rect.width > 0 && rect.height > 0;
        });
    }

    function getYuiGuideChatVisibleElementsFromSelectors(selectors) {
        var results = [];
        (Array.isArray(selectors) ? selectors : []).forEach(function (selector) {
            getYuiGuideChatVisibleElements(selector).forEach(function (element) {
                if (results.indexOf(element) < 0) {
                    results.push(element);
                }
            });
        });
        return results;
    }

    function getYuiGuideChatSpotlightItemTargets(kind) {
        var popover = document.getElementById('composer-tool-popover-compact');
        var quickbar = document.getElementById('composer-avatar-tool-quickbar');
        if (kind === 'tool-toggle') {
            return [getYuiGuideChatVisibleElement('.compact-input-tool-toggle')].filter(Boolean);
        }
        if (kind === 'history') {
            return [
                getYuiGuideChatVisibleElement('.compact-export-history-anchor'),
                getYuiGuideChatVisibleElement('.compact-input-tool-item-history', popover)
            ].filter(Boolean);
        }
        if (kind === 'avatar-tools') {
            return [
                getYuiGuideChatVisibleElement('.compact-input-tool-item-avatar', popover),
                getYuiGuideChatVisibleElement('[data-avatar-tool-id]', quickbar)
            ].filter(Boolean);
        }
        if (kind === 'avatar-tool-items') {
            return getYuiGuideChatVisibleElements('#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id]')
                .concat(getYuiGuideChatVisibleElements('#composer-avatar-tool-quickbar .composer-icon-button[data-avatar-tool-id]'))
                .concat(getYuiGuideChatVisibleElements('.composer-icon-button[data-avatar-tool-id]'))
                .filter(function (element, index, array) {
                    return array.indexOf(element) === index;
                });
        }
        if (kind === 'galgame') {
            return [
                getYuiGuideChatVisibleElement('.compact-input-tool-item-galgame', popover),
                getYuiGuideChatVisibleElement('.composer-galgame-btn', popover)
            ].filter(Boolean);
        }
        return [];
    }

    function getYuiGuideChatCursorTarget(kind, options) {
        var normalizedOptions = options || {};
        var targetIndex = Number.isFinite(Number(normalizedOptions.targetIndex))
            ? Math.max(0, Math.floor(Number(normalizedOptions.targetIndex)))
            : 0;
        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        var targets = entry && Array.isArray(entry.localSelectors)
            ? getYuiGuideChatVisibleElementsFromSelectors(entry.localSelectors)
            : getYuiGuideChatSpotlightItemTargets(kind);
        if (targets.length > 0) {
            return targets[Math.min(targetIndex, targets.length - 1)];
        }
        return getYuiGuideChatSpotlightTarget(kind);
    }

    function getYuiGuideChatCursorTargetPoint(kind, options) {
        var target = getYuiGuideChatCursorTarget(kind, options);
        var rect = target && typeof target.getBoundingClientRect === 'function'
            ? target.getBoundingClientRect()
            : null;
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        return {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2
        };
    }

    function getYuiGuideCompactToolWheelCenterPoint() {
        var fan = getYuiGuideChatVisibleElement('#react-chat-window-root .compact-input-tool-fan')
            || getYuiGuideChatVisibleElement('.compact-input-tool-fan');
        var rect = fan && typeof fan.getBoundingClientRect === 'function'
            ? fan.getBoundingClientRect()
            : null;
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        var style = window.getComputedStyle ? window.getComputedStyle(fan) : null;
        var readPixelVar = function (name, fallback) {
            var rawValue = style ? String(style.getPropertyValue(name) || '').trim() : '';
            var parsedValue = Number.parseFloat(rawValue);
            return Number.isFinite(parsedValue) ? parsedValue : fallback;
        };
        return {
            x: rect.left + readPixelVar('--compact-tool-wheel-center-x', 116),
            y: rect.top + readPixelVar('--compact-tool-wheel-center-y', 116)
        };
    }

    function buildYuiGuideChatCursorArcMotion(kind, options) {
        var normalizedOptions = options || {};
        var start = I.yuiGuideChatCursorPoint || getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        var center = kind === 'galgame'
            ? getYuiGuideCompactToolWheelCenterPoint()
            : getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        if (!center) {
            center = getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        }
        if (!start || !center) {
            return null;
        }
        var radius = Math.hypot(start.x - center.x, start.y - center.y);
        if (!Number.isFinite(radius) || radius < 4) {
            return null;
        }
        var direction = Number(normalizedOptions.direction) < 0 ? -1 : 1;
        var fraction = Number.isFinite(Number(normalizedOptions.fraction))
            ? Math.max(0, Math.min(1, Number(normalizedOptions.fraction)))
            : 0.2;
        var totalAngle = direction * Math.PI * 2 * fraction;
        var startAngle = Math.atan2(start.y - center.y, start.x - center.x);
        var stepCount = Number.isFinite(Number(normalizedOptions.stepCount))
            ? Math.max(2, Math.floor(Number(normalizedOptions.stepCount)))
            : 8;
        var points = [];
        for (var index = 1; index <= stepCount; index += 1) {
            var progress = index / stepCount;
            var angle = startAngle + totalAngle * progress;
            points.push({
                x: center.x + Math.cos(angle) * radius,
                y: center.y + Math.sin(angle) * radius
            });
        }
        return {
            start: start,
            points: points,
            finalPoint: points[points.length - 1]
        };
    }

    function ensureYuiGuideChatCursorElement() {
        var cursor = document.getElementById('yui-guide-chat-cursor');
        if (cursor) return cursor;
        if (!document.body) return null;
        cursor = document.createElement('div');
        cursor.id = 'yui-guide-chat-cursor';
        cursor.setAttribute('aria-hidden', 'true');
        cursor.style.position = 'fixed';
        cursor.style.left = '0';
        cursor.style.top = '0';
        cursor.style.width = '18px';
        cursor.style.height = '18px';
        cursor.style.borderRadius = '999px';
        cursor.style.background = 'rgba(80, 140, 255, 0.78)';
        cursor.style.boxShadow = '0 0 0 4px rgba(80, 140, 255, 0.22), 0 8px 18px rgba(0, 0, 0, 0.24)';
        cursor.style.pointerEvents = 'none';
        cursor.style.zIndex = '2147483600';
        cursor.style.opacity = '0';
        cursor.style.transform = 'translate3d(-9999px, -9999px, 0)';
        cursor.style.transitionProperty = 'transform, opacity, box-shadow, background-color';
        cursor.style.transitionTimingFunction = 'cubic-bezier(0.22, 1, 0.36, 1)';
        document.body.appendChild(cursor);
        return cursor;
    }

    function hideYuiGuideChatCursorElement() {
        var cursor = document.getElementById('yui-guide-chat-cursor');
        if (!cursor) return;
        cursor.style.opacity = '0';
        cursor.hidden = true;
    }

    function reportYuiGuideChatCursorAnchor(screenPoint, kind, effect, effectDurationMs, options) {
        if (!screenPoint) {
            return;
        }
        var message = {
            x: screenPoint.x,
            y: screenPoint.y,
            kind: kind || '',
            effect: typeof effect === 'string' ? effect : '',
            effectDurationMs: Math.max(0, Math.floor(Number(effectDurationMs) || 0)),
            source: 'external-chat',
            settled: !!(options && options.settled),
            timestamp: Date.now(),
            bypassDedup: true
        };
        I.postYuiGuideMessageToPet('yui_guide_chat_cursor_anchor', message);
    }

    function publishYuiGuideChatCursorAnchor(kind, point, options, settled) {
        if (!point) {
            return;
        }
        reportYuiGuideChatCursorAnchor(point, kind, options && options.effect, options && options.effectDurationMs, {
            settled: settled === true
        });
    }

    function rememberYuiGuideChatCursorScreenPoint(point, kind, options, settled) {
        if (!point) {
            return;
        }
        publishYuiGuideChatCursorAnchor(kind || '', point, options || {}, settled === true);
    }

    function moveYuiGuideChatCursor(kind, point, options) {
        if (!point) return false;
        var normalizedOptions = options || {};
        var duration = normalizedOptions && Number.isFinite(Number(normalizedOptions.durationMs))
            ? Math.max(0, Math.floor(Number(options.durationMs)))
            : 240;
        if (isYuiGuidePcCursorOnlyMode()) {
            var screenPoint = I.toYuiGuideScreenPoint(point.x, point.y);
            I.yuiGuideChatCursorPoint = { x: point.x, y: point.y };
            I.sendYuiGuidePcOverlayPatch({
                cursor: {
                    visible: true,
                    x: screenPoint.x,
                    y: screenPoint.y,
                    durationMs: duration,
                    effect: normalizedOptions.effect || '',
                    effectDurationMs: Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs) || 0))
                }
            }, false, {
                tutorialRunId: normalizedOptions.pcOverlayRunId
            });
            publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, duration === 0);
            if (duration > 0) {
                var anchorToken = I.yuiGuideChatCursorRequestToken;
                window.setTimeout(function () {
                    if (anchorToken !== I.yuiGuideChatCursorRequestToken) {
                        return;
                    }
                    publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, true);
                }, duration);
            }
            return true;
        }
        var cursor = ensureYuiGuideChatCursorElement();
        if (!cursor) return false;
        cursor.style.transitionDuration = duration + 'ms, 120ms, 120ms, 120ms';
        cursor.hidden = false;
        cursor.style.opacity = '1';
        cursor.style.transform = 'translate3d(' + Math.round(point.x - 9) + 'px, ' + Math.round(point.y - 9) + 'px, 0)';
        I.yuiGuideChatCursorPoint = { x: point.x, y: point.y };
        return true;
    }

    I.applyYuiGuideChatCursor = function applyYuiGuideChatCursor(kind, options) {
        var normalizedOptions = options || {};
        var freezePoint = normalizedOptions.freezePoint === true;
        if (freezePoint) {
            I.yuiGuideChatCursorRequestToken += 1;
            if (kind === 'galgame') {
                I.yuiGuideChatCursorArcRequestToken += 1;
            }
        } else {
            I.yuiGuideChatCursorRequestToken = I.yuiGuideChatCursorRequestToken + 1;
        }
        if (!kind) {
            if (isYuiGuidePcCursorOnlyMode() && normalizedOptions.preservePcOverlayCursor !== true) {
                I.sendYuiGuidePcOverlayPatch({
                    cursor: { visible: false }
                }, false, {
                    tutorialRunId: normalizedOptions.pcOverlayRunId,
                    allowCreateRun: !(normalizedOptions.allowCreatePcOverlayRun === false),
                    skipBegin: normalizedOptions.skipPcOverlayBegin === true
                });
            } else if (isYuiGuidePcCursorOnlyMode()) {
                I.yuiGuidePcOverlayCursor = null;
            }
            hideYuiGuideChatCursorElement();
            I.yuiGuideChatCursorPoint = null;
            return true;
        }
        if (isYuiGuidePcCursorOnlyMode()) {
            var targetPoint = getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
            var freezeKey = kind + ':' + (normalizedOptions.timestamp || '');
            var frozenScreenPoint = freezePoint ? I.yuiGuideChatCursorFrozenScreenPoints[freezeKey] : null;
            if (!targetPoint && !frozenScreenPoint) return false;
            var screenPoint = frozenScreenPoint || I.toYuiGuideScreenPoint(targetPoint.x, targetPoint.y);
            if (freezePoint && I.yuiGuideChatCursorFrozenScreenPoints[freezeKey]) {
                screenPoint = I.yuiGuideChatCursorFrozenScreenPoints[freezeKey];
            } else if (freezePoint) {
                I.yuiGuideChatCursorFrozenScreenPoints[freezeKey] = screenPoint;
            }
            var duration = Number.isFinite(Number(normalizedOptions.durationMs))
                ? Math.max(0, Math.floor(Number(normalizedOptions.durationMs)))
                : 240;
            if (targetPoint) {
                I.yuiGuideChatCursorPoint = { x: targetPoint.x, y: targetPoint.y };
            }
            I.sendYuiGuidePcOverlayPatch({
                cursor: {
                    visible: true,
                    x: screenPoint.x,
                    y: screenPoint.y,
                    durationMs: duration,
                    effect: normalizedOptions.effect || '',
                    effectDurationMs: Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs) || 0))
                }
            }, false, {
                tutorialRunId: normalizedOptions.pcOverlayRunId
            });
            publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, duration === 0);
            if (duration > 0) {
                var anchorToken = I.yuiGuideChatCursorRequestToken;
                window.setTimeout(function () {
                    if (anchorToken !== I.yuiGuideChatCursorRequestToken) {
                        return;
                    }
                    publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, true);
                }, duration);
            }
            return true;
        }
        return moveYuiGuideChatCursor(kind, getYuiGuideChatCursorTargetPoint(kind, normalizedOptions), normalizedOptions);
    }

    I.applyYuiGuideChatCursorDrag = function applyYuiGuideChatCursorDrag(kind, options) {
        I.yuiGuideChatCursorRequestToken = I.yuiGuideChatCursorRequestToken + 1;
        var start = I.yuiGuideChatCursorPoint || getYuiGuideChatCursorTargetPoint(kind, options || {});
        if (!start) return false;
        return moveYuiGuideChatCursor(kind, {
            x: start.x + (Number.isFinite(Number(options && options.deltaX)) ? Number(options.deltaX) : 0),
            y: start.y + (Number.isFinite(Number(options && options.deltaY)) ? Number(options.deltaY) : 0)
        }, options || {});
    }

    I.applyYuiGuideChatCursorArc = function applyYuiGuideChatCursorArc(kind, options) {
        I.yuiGuideChatCursorRequestToken = I.yuiGuideChatCursorRequestToken + 1;
        var cursorRequestToken = I.yuiGuideChatCursorRequestToken;
        var arcRequestToken = ++I.yuiGuideChatCursorArcRequestToken;
        var motion = buildYuiGuideChatCursorArcMotion(kind, options || {});
        if (!motion || !motion.finalPoint || motion.points.length === 0) return false;
        var duration = options && Number.isFinite(Number(options.durationMs))
            ? Math.max(0, Math.floor(Number(options.durationMs)))
            : 240;
        var effectDurationMs = options && Number.isFinite(Number(options.effectDurationMs))
            ? Math.max(0, Math.floor(Number(options.effectDurationMs)))
            : duration;
        var startMoved = moveYuiGuideChatCursor(kind, motion.start, Object.assign({}, options || {}, {
            durationMs: 0,
            effect: options && typeof options.effect === 'string' ? options.effect : '',
            effectDurationMs: effectDurationMs
        }));
        if (!startMoved) {
            return false;
        }
        var segmentDuration = Math.max(0, Math.round(duration / motion.points.length));
        motion.points.forEach(function (point, index) {
            window.setTimeout(function () {
                if (
                    arcRequestToken !== I.yuiGuideChatCursorArcRequestToken
                    || cursorRequestToken !== I.yuiGuideChatCursorRequestToken
                ) {
                    return;
                }
                moveYuiGuideChatCursor(kind, point, Object.assign({}, options || {}, {
                    durationMs: segmentDuration,
                    effectDurationMs: effectDurationMs
                }));
            }, index * segmentDuration);
        });
        var finalScreenPoint = I.toYuiGuideScreenPoint(motion.finalPoint.x, motion.finalPoint.y);
        window.setTimeout(function () {
            if (
                arcRequestToken !== I.yuiGuideChatCursorArcRequestToken
                || cursorRequestToken !== I.yuiGuideChatCursorRequestToken
            ) {
                return;
            }
            rememberYuiGuideChatCursorScreenPoint(finalScreenPoint, kind, options || {}, true);
        }, duration);
        return true;
    }

    I.clearYuiGuideChatSpotlightTracking = function clearYuiGuideChatSpotlightTracking() {
        if (I.yuiGuideChatSpotlightTimer) {
            I.yuiGuideChatSpotlightResources.clearInterval(I.yuiGuideChatSpotlightTimer);
            I.yuiGuideChatSpotlightTimer = 0;
        }
        I.yuiGuideChatSpotlightResources.destroy();
        I.yuiGuideChatSpotlightResources = I.createAppInterpageScopedResources();
    }

    function rememberYuiGuideChatPcSpotlightRects(kind, rects, variant) {
        I.yuiGuideChatSpotlightLastPcKind = kind || '';
        I.yuiGuideChatSpotlightLastPcVariant = typeof variant === 'string' ? variant : '';
        I.yuiGuideChatSpotlightLastPcRects = Array.isArray(rects)
            ? rects.map(function (rect) {
                return Object.assign({}, rect);
            })
            : [];
    }

    function clearYuiGuideChatPcSpotlightRects() {
        I.yuiGuideChatSpotlightLastPcKind = '';
        I.yuiGuideChatSpotlightLastPcVariant = '';
        I.yuiGuideChatSpotlightLastPcRects = [];
    }

    function cloneYuiGuideChatPcSpotlightRects() {
        return I.yuiGuideChatSpotlightLastPcRects.map(function (pcRect) {
            return Object.assign({}, pcRect);
        });
    }

    function preserveYuiGuideChatSpotlightDuringResistance(kind, pcOverlayRunId) {
        var preservedKind = typeof kind === 'string' && kind ? kind : I.yuiGuideChatSpotlightKind;
        if (!preservedKind) {
            return false;
        }
        if (
            I.yuiGuideChatSpotlightLastPcKind === preservedKind
            && I.yuiGuideChatSpotlightLastPcVariant === I.yuiGuideChatSpotlightVariant
            && I.yuiGuideChatSpotlightLastPcRects.length > 0
        ) {
            I.sendYuiGuidePcOverlayPatch({
                spotlights: cloneYuiGuideChatPcSpotlightRects()
            }, false, {
                tutorialRunId: pcOverlayRunId || I.yuiGuideChatSpotlightPcOverlayRunId
            });
            return true;
        }
        updateYuiGuideChatSpotlight(preservedKind, pcOverlayRunId || I.yuiGuideChatSpotlightPcOverlayRunId);
        return true;
    }

    function isYuiGuideChatInputSpotlightKind(kind) {
        return kind === 'input' || kind === 'capsule-input';
    }

    I.scheduleYuiGuideChatInputSpotlightRetry = function scheduleYuiGuideChatInputSpotlightRetry(kind, pcOverlayRunId) {
        var retryKind = typeof kind === 'string' && kind ? kind : I.yuiGuideChatSpotlightKind;
        var retryRunId = typeof pcOverlayRunId === 'string' && pcOverlayRunId
            ? pcOverlayRunId
            : I.yuiGuideChatSpotlightPcOverlayRunId;
        if (!isYuiGuideChatInputSpotlightKind(retryKind)) {
            return;
        }

        [80, 180, 360, 720, 1200].forEach(function (delayMs) {
            I.yuiGuideChatSpotlightResources.setTimeout(function () {
                if (I.yuiGuideChatSpotlightKind === retryKind) {
                    updateYuiGuideChatSpotlight(retryKind, retryRunId);
                }
            }, delayMs);
        });
    }

    function ensureYuiGuideChatSpotlightTracking(pcOverlayRunId) {
        if (typeof pcOverlayRunId === 'string' && pcOverlayRunId) {
            I.yuiGuideChatSpotlightPcOverlayRunId = pcOverlayRunId;
        }
        if (!I.yuiGuideChatSpotlightKind || I.yuiGuideChatSpotlightTimer) {
            return;
        }
        I.yuiGuideChatSpotlightTimer = I.yuiGuideChatSpotlightResources.setInterval(function () {
            updateYuiGuideChatSpotlight(I.yuiGuideChatSpotlightKind, I.yuiGuideChatSpotlightPcOverlayRunId);
        }, 120);
    }

    function updateYuiGuideChatSpotlight(kind, pcOverlayRunId) {
        var pcOverlayAvailable = I.isYuiGuidePcOverlayAvailable();
        var spotlight = I.getYuiGuideChatSpotlightElement(!pcOverlayAvailable);
        var patchOptions = {
            tutorialRunId: pcOverlayRunId || I.yuiGuideChatSpotlightPcOverlayRunId
        };

        var target = getYuiGuideChatSpotlightTarget(kind);
        var rect = target && typeof target.getBoundingClientRect === 'function'
            ? target.getBoundingClientRect()
            : null;
        var sourceRectInfo = rect ? getYuiGuideChatSpotlightSourceRect(kind, I.yuiGuideChatSpotlightVariant, rect) : null;
        var sourceRect = sourceRectInfo ? sourceRectInfo.rect : rect;

        if (!sourceRect || sourceRect.width <= 0 || sourceRect.height <= 0) {
            if (pcOverlayAvailable) {
                if (
                    kind
                    && I.yuiGuideChatSpotlightLastPcKind === kind
                    && I.yuiGuideChatSpotlightLastPcVariant === I.yuiGuideChatSpotlightVariant
                    && I.yuiGuideChatSpotlightLastPcRects.length > 0
                ) {
                    I.sendYuiGuidePcOverlayPatch({
                        spotlights: I.yuiGuideChatSpotlightLastPcRects.map(function (pcRect) {
                            return Object.assign({}, pcRect);
                        })
                    }, false, patchOptions);
                }
            }
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }

        var padding = kind === 'window' ? 10 : 8;
        var radius = kind === 'window' ? 26 : Math.min(34, Math.max(18, Math.round((sourceRect.height + padding * 2) / 2)));
        if (pcOverlayAvailable) {
            var pcRects = [I.toYuiGuideScreenRect({
                left: sourceRect.left - padding,
                top: sourceRect.top - padding,
                width: sourceRect.width + padding * 2,
                height: sourceRect.height + padding * 2
            }, kind, I.yuiGuideChatSpotlightVariant)].filter(Boolean);
            rememberYuiGuideChatPcSpotlightRects(kind, pcRects, I.yuiGuideChatSpotlightVariant);
            I.sendYuiGuidePcOverlayPatch({ spotlights: pcRects }, false, patchOptions);
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }
        if (!spotlight) {
            return;
        }
        spotlight.hidden = false;
        spotlight.classList.remove('is-window', 'is-input');
        spotlight.classList.add(kind === 'window' ? 'is-window' : 'is-input');
        spotlight.classList.add('is-visible');
        spotlight.style.left = Math.round(sourceRect.left - padding) + 'px';
        spotlight.style.top = Math.round(sourceRect.top - padding) + 'px';
        spotlight.style.width = Math.round(sourceRect.width + padding * 2) + 'px';
        spotlight.style.height = Math.round(sourceRect.height + padding * 2) + 'px';
        spotlight.style.borderRadius = radius + 'px';
    }

    I.applyYuiGuideChatSpotlight = function applyYuiGuideChatSpotlight(kind, options) {
        var normalizedKind = typeof kind === 'string' ? kind : '';
        var pcOverlayRunId = options && typeof options.pcOverlayRunId === 'string'
            ? options.pcOverlayRunId
            : '';
        var hasVariantOption = options && Object.prototype.hasOwnProperty.call(options, 'variant');
        var normalizedVariant = hasVariantOption && typeof options.variant === 'string'
            ? options.variant.trim()
            : (normalizedKind && normalizedKind === I.yuiGuideChatSpotlightKind ? I.yuiGuideChatSpotlightVariant : '');
        if (pcOverlayRunId) {
            I.yuiGuideChatSpotlightPcOverlayRunId = pcOverlayRunId;
        }
        if (normalizedKind && options && options.preserveDuringResistance === true) {
            if (I.yuiGuideChatSpotlightKind && I.yuiGuideChatSpotlightKind !== normalizedKind) {
                I.clearYuiGuideChatSpotlightTracking();
            }
            I.yuiGuideChatSpotlightKind = normalizedKind;
            I.yuiGuideChatSpotlightVariant = normalizedVariant;
            preserveYuiGuideChatSpotlightDuringResistance(normalizedKind, pcOverlayRunId);
            I.scheduleYuiGuideChatInputSpotlightRetry(normalizedKind, pcOverlayRunId);
            ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
            return;
        }
        if (
            !normalizedKind
            && options
            && options.preserveDuringResistance === true
            && I.yuiGuideChatSpotlightKind
            && I.yuiGuideChatSpotlightLastPcKind === I.yuiGuideChatSpotlightKind
            && I.yuiGuideChatSpotlightLastPcVariant === I.yuiGuideChatSpotlightVariant
            && I.yuiGuideChatSpotlightLastPcRects.length > 0
        ) {
            updateYuiGuideChatSpotlight(I.yuiGuideChatSpotlightKind, pcOverlayRunId);
            ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
            return;
        }
        I.yuiGuideChatSpotlightKind = normalizedKind;
        I.yuiGuideChatSpotlightVariant = normalizedKind ? normalizedVariant : '';
        I.clearYuiGuideChatSpotlightTracking();

        if (!I.yuiGuideChatSpotlightKind) {
            var clearSpotlightRunId = pcOverlayRunId || I.yuiGuideChatSpotlightPcOverlayRunId;
            clearYuiGuideChatPcSpotlightRects();
            I.yuiGuideChatSpotlightPcOverlayRunId = '';
            I.yuiGuideChatSpotlightVariant = '';
            I.sendYuiGuidePcOverlayPatch({ spotlights: [] }, false, {
                tutorialRunId: clearSpotlightRunId,
                allowCreateRun: !(options && options.allowCreatePcOverlayRun === false),
                skipBegin: options && options.skipPcOverlayBegin === true
            });
            var spotlight = I.getYuiGuideChatSpotlightElement(false);
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }

        updateYuiGuideChatSpotlight(I.yuiGuideChatSpotlightKind, pcOverlayRunId);
        ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
    }

    function applyYuiGuideChatCursorRelay(message) {
        if (!message || typeof message !== 'object' || !I.isStandaloneChatPage() || !document.body) return false;
        if (I.yuiGuidePcOverlayLifecycleClosed) {
            return false;
        }
        if (I.isYuiGuidePcOverlayRunEnded(message.tutorialRunId)) {
            return false;
        }
        var action = message.action || '';
        if (!I.isYuiGuideMessageForCurrentLifecycle(message)) {
            return false;
        }
        var cursorOptions = Object.assign({}, message, {
            pcOverlayRunId: message.pcOverlayRunId || I.getYuiGuidePcOverlayRunIdFromMessage(message)
        });
        if (action === 'yui_guide_drag_chat_cursor') {
            return I.applyYuiGuideChatCursorDrag(message.kind || '', cursorOptions);
        }
        if (action === 'yui_guide_arc_chat_cursor') {
            return I.applyYuiGuideChatCursorArc(message.kind || '', cursorOptions);
        }
        if (action === 'yui_guide_set_chat_cursor') {
            return I.applyYuiGuideChatCursor(message.kind || '', cursorOptions);
        }
        return false;
    }

    I.yuiGuideInterpageResources.addEventListener(window, 'neko:tutorial-overlay-relay', function (event) {
        applyYuiGuideChatCursorRelay(event && event.detail);
    });

    I.yuiGuideInterpageResources.addEventListener(window, 'message', function (event) {
        if (event.origin !== window.location.origin) return;
        var data = event.data || {};
        if (!data || data.action !== '__nekoTutorialOverlayRelay') return;
        applyYuiGuideChatCursorRelay(Object.assign({}, data.detail || {}, {
            freezePoint: event.data.freezePoint === true || (data.detail && data.detail.freezePoint === true)
        }));
    });

    I.clearYuiGuidePcOverlayBridgeState = function clearYuiGuidePcOverlayBridgeState(reason, tutorialRunId) {
        var rawReason = typeof reason === 'string' && reason ? reason : 'lifecycle-ended';
        var endedRunId = typeof tutorialRunId === 'string' && tutorialRunId
            ? tutorialRunId
            : I.getExistingYuiGuidePcOverlayRunId();
        if (endedRunId) {
            I.yuiGuidePcOverlayEndedRunId = endedRunId;
        }
        I.closeYuiGuidePcOverlayLifecycle();
        I.yuiGuidePcOverlayActive = false;
        I.yuiGuidePcOverlayReady = false;
        I.yuiGuidePcOverlayRunIdOverride = '';
        I.yuiGuidePcOverlaySpotlights = [];
        I.yuiGuidePcOverlayCursor = null;
        clearYuiGuideChatPcSpotlightRects();
        try {
            window.localStorage.removeItem('yuiGuidePcOverlayRunId');
        } catch (_) {}
        I.yuiGuideChatCursorRequestToken += 1;
        I.yuiGuideChatCursorArcRequestToken += 1;
        I.yuiGuideCompactToolWheelRotateRetryToken += 1;
        I.applyYuiGuideChatSpotlight('', {
            pcOverlayRunId: endedRunId,
            allowCreatePcOverlayRun: false,
            skipPcOverlayBegin: true
        });
        I.applyYuiGuideChatCursor('', {
            pcOverlayRunId: endedRunId,
            allowCreatePcOverlayRun: false,
            skipPcOverlayBegin: true
        });
        I.applyYuiGuideChatLockState(false);
        I.applyYuiGuideChatInputLocked(false, rawReason);
        I.applyYuiGuideCompactChatFixedLayout(false);
        I.applyYuiGuideAvatarToolMenuOpen(false, rawReason);
        I.applyYuiGuideCompactHistoryOpen(false, rawReason);
        I.applyYuiGuideCompactToolFanOpen(false, rawReason);
        I.clearYuiGuideChatMessages();
        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.clear === 'function'
        ) {
            try {
                var clearResult = window.nekoTutorialOverlay.clear({
                    reason: rawReason,
                    tutorialRunId: endedRunId
                });
                Promise.resolve(clearResult).then(function (result) {
                    if (result && (result.stale === true || result.ok === false)) {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    }
                }).catch(function () {
                    try {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    } catch (_) {}
                });
            } catch (_) {}
        }
    }

    // =====================================================================
    // Cross-window handoff event forwarding via BroadcastChannel
    // =====================================================================

    // 首页发出 handoff-sent DOM 事件时，转发到 BC 让其他标签页感知

    Object.assign(window.appInterpage, I.mod || {});
})();
