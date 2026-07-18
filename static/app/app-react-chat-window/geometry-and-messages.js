/**
 * app-react-chat-window/geometry-and-messages.js
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
    function getCompactSurfaceDesktopWindowY() {
        var windowBounds = I.getCompactSurfaceDesktopWindowBounds();
        var y = Number(windowBounds && windowBounds.y);
        return Number.isFinite(y) ? y : 0;
    }

    function getCompactSurfaceDesktopScreenRect() {
        var layoutOverride = I.getElectronCompactLayoutOverride();
        return layoutOverride && layoutOverride.surfaceScreenRect
            ? layoutOverride.surfaceScreenRect
            : null;
    }

    function getCompactDesktopWorkAreaEdge(workArea, edge) {
        if (!workArea) return NaN;
        var explicit = Number(workArea[edge]);
        if (Number.isFinite(explicit)) return explicit;
        var x = Number(workArea.x);
        var y = Number(workArea.y);
        var width = Number(workArea.width);
        var height = Number(workArea.height);
        if (edge === 'left' && Number.isFinite(x)) return x;
        if (edge === 'top' && Number.isFinite(y)) return y;
        if (edge === 'right' && Number.isFinite(x) && Number.isFinite(width)) return x + width;
        if (edge === 'bottom' && Number.isFinite(y) && Number.isFinite(height)) return y + height;
        return NaN;
    }

    function clampCompactSurfaceResizeWidthForSide(side, desiredWidth, currentRect) {
        var width = Number(desiredWidth);
        if (!Number.isFinite(width) || width <= 0) {
            width = currentRect && currentRect.width ? currentRect.width : I.COMPACT_SURFACE_MAX_WIDTH;
        }
        var layoutOverride = I.getElectronCompactLayoutOverride();
        var sideMax;
        if (layoutOverride && layoutOverride.windowBounds && layoutOverride.workArea) {
            var windowBounds = layoutOverride.windowBounds;
            var workArea = layoutOverride.workArea;
            var anchorLeftScreen = I.compactSurfaceResizeSession
                ? I.compactSurfaceResizeSession.anchorLeftScreen
                : currentRect.left + windowBounds.x;
            var anchorRightScreen = I.compactSurfaceResizeSession
                ? I.compactSurfaceResizeSession.anchorRightScreen
                : currentRect.left + currentRect.width + windowBounds.x;
            var workAreaLeft = getCompactDesktopWorkAreaEdge(workArea, 'left');
            var workAreaRight = getCompactDesktopWorkAreaEdge(workArea, 'right');
            sideMax = side === 'left'
                ? anchorRightScreen - (workAreaLeft + I.COMPACT_SURFACE_VIEWPORT_PAD_X)
                : (workAreaRight - I.COMPACT_SURFACE_VIEWPORT_PAD_X) - anchorLeftScreen;
            if (!Number.isFinite(sideMax) || sideMax <= 0) {
                sideMax = currentRect && currentRect.width ? currentRect.width : I.COMPACT_SURFACE_MAX_WIDTH;
            }
        } else {
            var minLeft = I.isMobileWidth() ? 8 : I.COMPACT_SURFACE_VIEWPORT_PAD_X;
            var maxRight = window.innerWidth - minLeft;
            sideMax = side === 'left'
                ? (currentRect.left + currentRect.width) - minLeft
                : maxRight - currentRect.left;
        }
        var minWidth = I.isMobileWidth()
            ? I.getCompactSurfaceMobileWidthBounds().minWidth
            : I.COMPACT_SURFACE_DESKTOP_MIN_WIDTH;
        var maxWidth = Math.max(
            minWidth,
            Math.min(I.getCompactSurfaceResizeMaxWidth(), sideMax)
        );
        return Math.round(Math.max(minWidth, Math.min(width, maxWidth)));
    }

    I.applyCompactSurfaceResizeRequest = function applyCompactSurfaceResizeRequest(detail) {
        if (!I.isHomeCompactSurfaceRoute() && !I.isDesktopHomeCompactSurfaceRoute()) return;
        var side = detail && detail.side === 'left' ? 'left' : 'right';
        var phase = detail && detail.phase;
        if (I.isElectronChatWindow() && detail && detail.screenRect) {
            I.compactSurfaceDesktopResizeActive = phase !== 'end';
            if (phase === 'end') {
                I.compactSurfaceResizeSession = null;
            }
            window.dispatchEvent(new CustomEvent('neko:compact-surface-layout-change', {
                detail: {
                    screenRect: detail.screenRect,
                    resizeActive: phase !== 'end',
                    dragging: false,
                    reason: phase === 'end' ? 'resize-end' : 'resize'
                }
            }));
            return;
        }
        var currentRect = I.getCurrentCompactSurfaceRect();
        if (!currentRect) return;
        var windowX = I.getCompactSurfaceDesktopWindowX();
        var desktopSurfaceRect = getCompactSurfaceDesktopScreenRect();
        if (phase === 'start' || !I.compactSurfaceResizeSession || I.compactSurfaceResizeSession.side !== side) {
            I.compactSurfaceResizeSession = {
                side: side,
                anchorLeftScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.left
                    : currentRect.left + windowX,
                anchorRightScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.right
                    : currentRect.left + currentRect.width + windowX,
                anchorTopScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.top
                    : currentRect.top + getCompactSurfaceDesktopWindowY()
            };
        }
        var width = clampCompactSurfaceResizeWidthForSide(side, detail && detail.width, currentRect);
        var left = side === 'left'
            ? I.compactSurfaceResizeSession.anchorRightScreen - windowX - width
            : I.compactSurfaceResizeSession.anchorLeftScreen - windowX;
        var appliedRect = I.applyCompactSurfaceRect(left, currentRect.top, width, currentRect.height, {
            persist: phase === 'end'
        });
        if (appliedRect && detail && typeof detail === 'object') {
            try {
                detail.screenRect = I.getCompactSurfaceResizeScreenRect(appliedRect);
            } catch (_) {}
        }
        if (phase === 'end' && I.isElectronChatWindow()) {
            I.saveCompactSurfaceWidth(width);
        }
        if (phase === 'end') {
            I.compactSurfaceResizeSession = null;
        }
    }

    I.getCompactSurfaceTarget = function getCompactSurfaceTarget(layoutOverride) {
        layoutOverride = layoutOverride || I.getElectronCompactLayoutOverride();
        if (layoutOverride && layoutOverride.surface) {
            var overrideMetrics = I.getCompactSurfaceMetrics();
            return {
                width: layoutOverride.surface.width,
                height: overrideMetrics.height,
                left: layoutOverride.surface.left,
                top: layoutOverride.surface.top
            };
        }

        var metrics = I.getCompactSurfaceMetrics();
        var viewportWidth = window.innerWidth;
        var viewportHeight = window.innerHeight;
        var fallbackBottomGap = I.isCompactOnlyElectronRuntimeChatHost()
            ? Math.max(I.COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM, I.COMPACT_SURFACE_ELECTRON_DEFAULT_BOTTOM_GAP)
            : I.COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM;
        var fallbackTop = Math.max(
            I.COMPACT_SURFACE_VIEWPORT_PAD_TOP,
            viewportHeight - metrics.height - fallbackBottomGap
        );

        if (I.isElectronChatWindow()) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: Math.max(
                    I.COMPACT_SURFACE_VIEWPORT_PAD_X,
                    Math.min(
                        Math.round((viewportWidth - metrics.width) / 2),
                        viewportWidth - metrics.width - I.COMPACT_SURFACE_VIEWPORT_PAD_X
                    )
                ),
                top: fallbackTop
            };
        }

        var storedPosition = I.loadCompactSurfacePosition(metrics);
        if (storedPosition) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: storedPosition.left,
                top: storedPosition.top
            };
        }

        var avatarBounds = I.getCompactMinimizeBallAvatarBounds();
        if (!I.isMobileWidth() && avatarBounds) {
            var avatarLeft = avatarBounds.left + avatarBounds.width / 2 - metrics.width / 2;
            var avatarTop = avatarBounds.top + avatarBounds.height * I.COMPACT_SURFACE_AVATAR_VERTICAL_RATIO - metrics.height / 2;
            var avatarClamped = I.clampCompactSurfacePosition(
                Math.round(avatarLeft),
                Math.round(avatarTop),
                metrics
            );
            return {
                width: metrics.width,
                height: metrics.height,
                left: avatarClamped.left,
                top: avatarClamped.top
            };
        }

        if (I.isMobileWidth()) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: 8,
                top: fallbackTop
            };
        }

        return {
            width: metrics.width,
            height: metrics.height,
            left: Math.max(
                I.COMPACT_SURFACE_VIEWPORT_PAD_X,
                Math.min(
                    Math.round((viewportWidth - metrics.width) / 2),
                    viewportWidth - metrics.width - I.COMPACT_SURFACE_VIEWPORT_PAD_X
                )
            ),
            top: fallbackTop
        };
    }

    I.normalizeCompactDomRect = function normalizeCompactDomRect(rect) {
        if (!rect) return null;
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return null;
        }
        if (width <= 0 || height <= 0) return null;
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + width,
            bottom: Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + height
        };
    }

    I.getCompactSurfaceBaseRect = function getCompactSurfaceBaseRect() {
        var root = I.getRoot();
        var compactSurfaceShell = root ? root.querySelector('.compact-chat-surface-shell') : null;
        if (compactSurfaceShell && shouldIncludeCompactGeometryElement(compactSurfaceShell)) {
            var shellRect = I.normalizeCompactDomRect(compactSurfaceShell.getBoundingClientRect());
            if (shellRect) return shellRect;
        }
        var candidates = [
            '[data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
            '[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]'
        ];
        for (var i = 0; i < candidates.length; i += 1) {
            var element = document.querySelector(candidates[i]);
            if (!element || (root && !root.contains(element))) continue;
            if (!shouldIncludeCompactGeometryElement(element)) continue;
            var rect = I.normalizeCompactDomRect(element.getBoundingClientRect());
            if (rect) return rect;
        }
        return null;
    }

    function unionCompactRects(rects) {
        var valid = (rects || []).filter(Boolean);
        if (!valid.length) return null;
        var left = valid.reduce(function (min, rect) { return Math.min(min, rect.left); }, valid[0].left);
        var top = valid.reduce(function (min, rect) { return Math.min(min, rect.top); }, valid[0].top);
        var right = valid.reduce(function (max, rect) { return Math.max(max, rect.right); }, valid[0].right);
        var bottom = valid.reduce(function (max, rect) { return Math.max(max, rect.bottom); }, valid[0].bottom);
        return {
            left: left,
            top: top,
            width: right - left,
            height: bottom - top,
            right: right,
            bottom: bottom
        };
    }

    function getIdleCat1CompactMirrorNativeReserveRect(mirrorRect, surfaceRect) {
        var mirror = I.normalizeCompactDomRect(mirrorRect);
        var surface = I.normalizeCompactDomRect(surfaceRect);
        if (!mirror) return null;
        if (!surface) return mirror;
        var horizontalPad = Math.ceil(mirror.width / 2);
        var left = Math.round(surface.left - horizontalPad);
        var right = Math.round(surface.right + horizontalPad);
        var top = Math.round(Math.min(mirror.top, surface.top));
        var bottom = Math.round(Math.max(mirror.bottom, surface.top));
        return I.normalizeCompactDomRect({
            left: left,
            top: top,
            width: Math.max(1, right - left),
            height: Math.max(1, bottom - top)
        });
    }

    function intersectCompactRects(a, b) {
        var leftRect = I.normalizeCompactDomRect(a);
        var rightRect = I.normalizeCompactDomRect(b);
        if (!leftRect || !rightRect) return null;
        var left = Math.max(leftRect.left, rightRect.left);
        var top = Math.max(leftRect.top, rightRect.top);
        var right = Math.min(leftRect.right, rightRect.right);
        var bottom = Math.min(leftRect.bottom, rightRect.bottom);
        if (right <= left || bottom <= top) return null;
        return {
            left: left,
            top: top,
            width: right - left,
            height: bottom - top,
            right: right,
            bottom: bottom
        };
    }

    function shouldIncludeCompactGeometryElement(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') return false;
        var item = element.getAttribute('data-compact-geometry-item') || '';
        if (item === 'choice' && element.getAttribute('data-choice-layer-open') !== 'true') return false;
        if (item === 'toolFan' && element.getAttribute('data-compact-input-tool-fan-open') !== 'true') return false;
        if (element.getAttribute('aria-hidden') === 'true' && item !== 'resizeHandle' && item !== 'cat1Mirror') return false;
        var style = window.getComputedStyle ? window.getComputedStyle(element) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
        return true;
    }

    function getCompactGeometryElementRect(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') return null;
        var item = element.getAttribute('data-compact-geometry-item') || '';
        if (item === 'choice') {
            var choiceRects = Array.prototype.slice.call(element.querySelectorAll('.composer-galgame-option'))
                .map(function (child) {
                    var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                    if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                    return I.normalizeCompactDomRect(child.getBoundingClientRect());
                })
                .filter(Boolean);
            return unionCompactRects(choiceRects);
        }
        var ownRect = I.normalizeCompactDomRect(element.getBoundingClientRect());
        if (ownRect) return ownRect;

        var childRects = Array.prototype.slice.call(element.querySelectorAll('button'))
            .map(function (child) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                return I.normalizeCompactDomRect(child.getBoundingClientRect());
            })
            .filter(Boolean);
        return unionCompactRects(childRects);
    }

    function getCompactHistoryScrollbarRect(element, parentRect) {
        if (!element || !parentRect) return null;
        var scrollNode = element.querySelector('.compact-export-history-scroll');
        if (!scrollNode || typeof scrollNode.getBoundingClientRect !== 'function') return null;
        if (scrollNode.scrollHeight <= scrollNode.clientHeight + 1) return null;
        if (scrollNode.getAttribute('data-compact-scrollbar-visible') !== 'true') return null;
        var style = window.getComputedStyle ? window.getComputedStyle(scrollNode) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
        var scrollbarHit = element.querySelector('.compact-export-history-scrollbar-hit');
        if (scrollbarHit && typeof scrollbarHit.getBoundingClientRect === 'function') {
            var hitStyle = window.getComputedStyle ? window.getComputedStyle(scrollbarHit) : null;
            if (hitStyle && hitStyle.pointerEvents === 'none') return null;
            if (!hitStyle || (
                hitStyle.display !== 'none'
                && hitStyle.visibility !== 'hidden'
                && hitStyle.pointerEvents !== 'none'
            )) {
                var hitRect = intersectCompactRects(scrollbarHit.getBoundingClientRect(), parentRect);
                if (hitRect) return hitRect;
            }
        }
        var scrollRect = intersectCompactRects(scrollNode.getBoundingClientRect(), parentRect);
        if (!scrollRect) return null;
        var gutterWidth = Math.min(Math.max(Number(scrollNode.offsetWidth - scrollNode.clientWidth) || 0, 8), 14);
        return {
            left: scrollRect.right - gutterWidth,
            top: scrollRect.top,
            width: gutterWidth,
            height: scrollRect.height,
            right: scrollRect.right,
            bottom: scrollRect.bottom
        };
    }

    var COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT = 18;
    var COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_X = 6;
    var COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_Y = 12;

    function readCompactToolFanPixelVar(style, name, fallback) {
        var rawValue = style ? style.getPropertyValue(name) : '';
        var parsedValue = parseFloat(rawValue);
        return Number.isFinite(parsedValue) ? parsedValue : fallback;
    }

    function buildCompactToolFanCircleSliceRects(rect, element) {
        if (!rect) return null;
        var style = window.getComputedStyle ? window.getComputedStyle(element) : null;
        var centerX = rect.left + readCompactToolFanPixelVar(style, '--compact-tool-wheel-center-x', 116);
        var centerY = rect.top + readCompactToolFanPixelVar(style, '--compact-tool-wheel-center-y', 116);
        var radius = readCompactToolFanPixelVar(style, '--compact-tool-wheel-hover-radius', 116);
        if (!Number.isFinite(radius) || radius <= 0) return null;
        var sliceHeight = (radius * 2) / COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT;
        var slices = [];
        for (var index = 0; index < COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT; index += 1) {
            var top = centerY - radius + (sliceHeight * index);
            var bottom = index === COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT - 1
                ? centerY + radius
                : top + sliceHeight;
            var middleY = (top + bottom) / 2;
            var halfWidth = Math.sqrt(Math.max(0, (radius * radius) - ((middleY - centerY) * (middleY - centerY))));
            var left = centerX - halfWidth;
            var right = centerX + halfWidth;
            slices.push({
                left: left,
                top: top,
                width: right - left,
                height: bottom - top,
                right: right,
                bottom: bottom
            });
        }
        return slices;
    }

    function expandCompactRect(rect, expandX, expandTop, expandBottom) {
        if (!rect) return null;
        var left = rect.left - expandX;
        var top = rect.top - expandTop;
        var right = rect.right + expandX;
        var bottom = rect.bottom + expandBottom;
        return {
            left: left,
            top: top,
            width: right - left,
            height: bottom - top,
            right: right,
            bottom: bottom
        };
    }

    function buildCompactAvatarToolChoiceHitRect(rect) {
        return expandCompactRect(
            rect,
            COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_X,
            COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_Y,
            COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_Y
        );
    }

    function isCompactSurfaceBaseAnchorKind(kind) {
        return kind === 'surfaceShell' || kind === 'capsule' || kind === 'input';
    }

    function isCompactSurfaceBaseHitKind(kind) {
        return kind === 'inputControl';
    }

    function getCompactSurfaceGeometryRole(kind) {
        if (isCompactSurfaceBaseAnchorKind(kind)) return 'baseAnchor';
        if (isCompactSurfaceBaseHitKind(kind)) return 'baseHit';
        return 'extraIsland';
    }

    function assignCompactSurfaceGeometryRole(item) {
        if (!item) return item;
        item.geometryRole = getCompactSurfaceGeometryRole(item.kind);
        return item;
    }

    function getCompactGeometryItemBoundsRects(item) {
        if (!item) return [];
        var rects = [];
        if (item.visualRect) rects.push(item.visualRect);
        if (item.nativeRect) rects.push(item.nativeRect);
        return rects;
    }

    function collectCompactToolFanGeometryItems(element) {
        if (!element || element.getAttribute('data-compact-geometry-item') !== 'toolFan') return [];
        var parentRect = getCompactGeometryElementRect(element);
        var items = [];
        if (parentRect) {
            var nativeRects = buildCompactToolFanCircleSliceRects(parentRect, element) || [parentRect];
            nativeRects.forEach(function (nativeRect, index) {
                items.push({
                    id: index === 0 ? 'toolFan:native' : 'toolFan:native:' + index,
                    owner: 'surface',
                    kind: 'toolFan',
                    visualRect: nativeRect,
                    hitRect: nativeRect,
                    nativeRect: nativeRect,
                    interactive: true
                });
            });
        }
        return items.concat(Array.prototype.slice.call(element.querySelectorAll('.compact-input-tool-item, .composer-icon-popover .composer-icon-button, .avatar-tool-quickbar .composer-icon-button, .avatar-tool-quickbar-edit'))
            .reduce(function (collectedItems, child, index) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return collectedItems;
                if (style && Number(style.opacity) <= 0.01) return collectedItems;
                var slot = child.getAttribute('data-compact-tool-wheel-slot') || '';
                var isAvatarToolChoice = child.classList && (child.classList.contains('composer-icon-button') || child.classList.contains('avatar-tool-quickbar-edit'));
                if (!isAvatarToolChoice && (!slot || slot.indexOf('hidden') === 0)) return collectedItems;
                var rect = I.normalizeCompactDomRect(child.getBoundingClientRect());
                if (!rect) return collectedItems;
                var hitRect = isAvatarToolChoice ? buildCompactAvatarToolChoiceHitRect(rect) : rect;
                if (!hitRect) return collectedItems;
                var itemId = isAvatarToolChoice
                    ? 'toolFan:avatarToolChoice:' + index
                    : 'toolFan:' + slot + ':' + index;
                collectedItems.push({
                    id: isAvatarToolChoice
                        ? 'toolFan:avatarToolChoice:' + index
                        : 'toolFan:' + slot + ':' + index,
                    owner: 'surface',
                    kind: 'toolFan',
                    visualRect: rect,
                    hitRect: hitRect,
                    nativeRect: hitRect,
                    interactive: true
                });
                var tooltip = child.querySelector && child.querySelector('.compact-input-tool-tooltip');
                if (!tooltip) return collectedItems;
                var tooltipStyle = window.getComputedStyle ? window.getComputedStyle(tooltip) : null;
                if (tooltipStyle && (tooltipStyle.display === 'none' || tooltipStyle.visibility === 'hidden')) return collectedItems;
                if (tooltipStyle && Number(tooltipStyle.opacity) <= 0.01) return collectedItems;
                var tooltipRect = I.normalizeCompactDomRect(tooltip.getBoundingClientRect());
                if (!tooltipRect) return collectedItems;
                collectedItems.push({
                    id: itemId + ':tooltip',
                    owner: 'surface',
                    kind: 'toolFan',
                    visualRect: tooltipRect,
                    hitRect: null,
                    nativeRect: tooltipRect,
                    interactive: false
                });
                return collectedItems;
            }, []));
    }

    function collectCompactInputSurfaceGeometryItems(element) {
        var parentRect = getCompactGeometryElementRect(element);
        var inputSurfaceIsDragSurface = element.getAttribute('data-compact-drag-surface') === 'true';
        var items = [];
        if (parentRect) {
            items.push({
                id: element.id || 'input:surface',
                owner: 'surface',
                kind: 'input',
                visualRect: parentRect,
                hitRect: inputSurfaceIsDragSurface ? parentRect : null,
                nativeRect: inputSurfaceIsDragSurface ? parentRect : null,
                interactive: inputSurfaceIsDragSurface
            });
        }
        var hitRegionElements = [];
        if (element.getAttribute('data-compact-hit-region') === 'true') {
            hitRegionElements.push(element);
        }
        hitRegionElements = hitRegionElements.concat(
            Array.prototype.slice.call(element.querySelectorAll('[data-compact-hit-region="true"]'))
        );
        return items.concat(hitRegionElements
            .map(function (child, index) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                if (style && Number(style.opacity) <= 0.01) return null;
                if (style && style.pointerEvents === 'none') return null;
                var rect = I.normalizeCompactDomRect(child.getBoundingClientRect());
                if (!rect) return null;
                var clippedRect = parentRect ? intersectCompactRects(rect, parentRect) : rect;
                if (!clippedRect) return null;
                return {
                    id: child.getAttribute('data-compact-hit-region-id') || ('input:hit:' + index),
                    owner: 'surface',
                    kind: 'inputControl',
                    visualRect: clippedRect,
                    hitRect: clippedRect,
                    nativeRect: clippedRect,
                    interactive: true,
                    hitRegionKind: child.getAttribute('data-compact-hit-region-kind') || null
                };
            })
            .filter(Boolean));
    }

    function collectCompactCompositeGeometryItems(element, kind) {
        var parentRect = getCompactGeometryElementRect(element);
        var items = [];
        if (parentRect) {
            items.push({
                id: kind + ':native',
                owner: 'surface',
                kind: kind || 'unknown',
                visualRect: parentRect,
                hitRect: null,
                nativeRect: parentRect,
                interactive: false
            });
            if (kind === 'history') {
                var scrollbarRect = getCompactHistoryScrollbarRect(element, parentRect);
                if (scrollbarRect) {
                    items.push({
                        id: 'history:scrollbar',
                        owner: 'surface',
                        kind: kind || 'unknown',
                        visualRect: scrollbarRect,
                        hitRect: scrollbarRect,
                        nativeRect: scrollbarRect,
                        interactive: true,
                        hitRegionKind: 'scrollbar'
                    });
                }
            }
        }
        return items.concat(Array.prototype.slice.call(element.querySelectorAll('[data-compact-hit-region="true"]'))
            .map(function (child, index) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                if (style && Number(style.opacity) <= 0.01) return null;
                var rect = I.normalizeCompactDomRect(child.getBoundingClientRect());
                if (!rect) return null;
                var clippedRect = kind === 'musicPlayer' || kind === 'meme'
                    ? rect
                    : (parentRect ? intersectCompactRects(rect, parentRect) : rect);
                if (!clippedRect) return null;
                var interactive = style ? style.pointerEvents !== 'none' : true;
                if (!interactive) return null;
                var hitRegionKind = child.getAttribute('data-compact-hit-region-kind') || null;
                return {
                    id: child.getAttribute('data-compact-hit-region-id') || (kind + ':hit:' + index),
                    owner: 'surface',
                    kind: kind || 'unknown',
                    visualRect: clippedRect,
                    hitRect: clippedRect,
                    nativeRect: clippedRect,
                    interactive: true,
                    hitRegionKind: hitRegionKind
                };
            })
            .filter(Boolean));
    }

    function collectCompactSurfaceGeometryItems() {
        var root = I.getRoot();
        if (!root) return [];
        var compactSurfaceShell = root.querySelector('.compact-chat-surface-shell');
        var shellRect = compactSurfaceShell
            ? I.normalizeCompactDomRect(compactSurfaceShell.getBoundingClientRect())
            : null;
        var elements = Array.prototype.slice.call(document.querySelectorAll('[data-compact-geometry-owner="surface"]'));
        var initialItems = [];
        if (shellRect) {
            initialItems.push({
                id: 'surface:shell',
                owner: 'surface',
                kind: 'surfaceShell',
                visualRect: shellRect,
                hitRect: null,
                nativeRect: null,
                interactive: false
            });
        }
        return elements.reduce(function (items, element) {
            if (element.classList.contains('avatar-tool-manager-dialog')) {
                var dialogRect = getCompactGeometryElementRect(element);
                if (dialogRect) {
                    items.push({
                        id: 'avatarToolManagerDialog',
                        owner: 'surface',
                        kind: 'avatarToolManager',
                        visualRect: dialogRect,
                        hitRect: dialogRect,
                        nativeRect: dialogRect,
                        interactive: true
                    });
                }
                return items;
            }
            if (
                !root.contains(element)
                && !element.classList.contains('compact-input-tool-fan')
                && !element.classList.contains('compact-chat-choice-anchor')
                && !element.classList.contains('neko-idle-cat1-compact-mirror')
            ) return items;
            if (!shouldIncludeCompactGeometryElement(element)) return items;
            var compactGeometryItem = element.getAttribute('data-compact-geometry-item');
            if (compactGeometryItem === 'toolFan') {
                return items.concat(collectCompactToolFanGeometryItems(element));
            }
            if (compactGeometryItem === 'input') {
                return items.concat(collectCompactInputSurfaceGeometryItems(element));
            }
            if (compactGeometryItem === 'cat1Mirror') {
                var mirrorRect = getCompactGeometryElementRect(element);
                var mirrorNativeRect = getIdleCat1CompactMirrorNativeReserveRect(mirrorRect, shellRect);
                if (mirrorRect) {
                    items.push({
                        id: 'cat1Mirror:native',
                        owner: 'surface',
                        kind: 'cat1Mirror',
                        visualRect: mirrorRect,
                        hitRect: null,
                        nativeRect: mirrorNativeRect || mirrorRect,
                        interactive: false
                    });
                }
                return items;
            }
            if (element.getAttribute('data-compact-geometry-hit-scope') === 'children') {
                return items.concat(collectCompactCompositeGeometryItems(element, compactGeometryItem));
            }
            var rect = getCompactGeometryElementRect(element);
            if (!rect) return items;
            items.push({
                id: element.id || compactGeometryItem || element.className || 'compact-item',
                owner: 'surface',
                kind: compactGeometryItem || 'unknown',
                visualRect: rect,
                hitRect: rect,
                nativeRect: rect,
                interactive: true
            });
            return items;
        }, initialItems).map(assignCompactSurfaceGeometryRole);
    }

    function getCompactInteractionGeometrySnapshot() {
        if (!I.isHomeCompactMinimizeBallRoute()) return null;
        var layoutOverride = I.getElectronCompactLayoutOverride();
        var surfaceItems = I.isCompactHomeMinimizeBallEnabled() ? collectCompactSurfaceGeometryItems() : [];
        var baseSurfaceItems = surfaceItems.filter(function (item) {
            return item && item.geometryRole === 'baseAnchor';
        });
        var extraIslandItems = surfaceItems.filter(function (item) {
            return item && item.geometryRole === 'extraIsland';
        });
        var baseSurfaceNativeItems = surfaceItems.filter(function (item) {
            return item && (item.geometryRole === 'baseAnchor' || item.geometryRole === 'baseHit');
        });
        var surfaceRects = surfaceItems.reduce(function (rects, item) {
            return rects.concat(getCompactGeometryItemBoundsRects(item));
        }, []);
        var baseSurfaceRects = baseSurfaceItems.reduce(function (rects, item) {
            return rects.concat(getCompactGeometryItemBoundsRects(item));
        }, []);
        // compact 态不再渲染模型旁的悬浮最小化球，故不再上报其 hit/native 区域，
        // 避免 Electron 桌面壳为一个不可见的球保留点击区域（externalBall 仍走桌面外部球）。
        var ballRect = null;
        return {
            mode: I.getCurrentChatSurfaceMode(),
            compactChatState: I.getCurrentCompactChatState(),
            viewport: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            surfaceItems: surfaceItems,
            surfaceUnion: unionCompactRects(surfaceRects),
            baseSurfaceItems: baseSurfaceItems,
            baseSurfaceRect: unionCompactRects(baseSurfaceRects),
            baseSurfaceNativeRects: baseSurfaceNativeItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            baseSurfaceHitRects: baseSurfaceNativeItems
                .map(function (item) { return item.hitRect; })
                .filter(Boolean),
            extraIslandItems: extraIslandItems,
            extraIslandNativeRects: extraIslandItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            extraIslandHitRects: extraIslandItems.map(function (item) { return item.hitRect; }).filter(Boolean),
            surfaceHitRects: surfaceItems.map(function (item) { return item.hitRect; }).filter(Boolean),
            surfaceNativeRects: surfaceItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            compactChoicePlacement: layoutOverride ? layoutOverride.compactChoicePlacement : null,
            ballRect: ballRect,
            externalBall: I.isElectronCompactExternalBallEnabled()
                ? (layoutOverride && layoutOverride.ball) || I.normalizeCompactDomRect(window.__nekoDesktopCompactBallScreenRect)
                : null
        };
    }

    I.syncCompactInteractionGeometry = function syncCompactInteractionGeometry() {
        var snapshot = getCompactInteractionGeometrySnapshot();
        var serialized = snapshot ? JSON.stringify(snapshot) : '';
        if (serialized === I.compactInteractionGeometrySnapshot) return;
        I.compactInteractionGeometrySnapshot = serialized;
        window.__nekoCompactInteractionGeometry = snapshot;
        window.__nekoGetCompactInteractionGeometry = getCompactInteractionGeometrySnapshot;
        window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-change', {
            detail: snapshot
        }));
    }

    function clearCompactSurfaceAnchor() {
        var shell = I.getShell();
        if (!shell) return;
        shell.style.removeProperty('--compact-surface-left');
        shell.style.removeProperty('--compact-surface-top');
        shell.style.removeProperty('--compact-surface-width');
        shell.style.removeProperty('--compact-surface-height');
        shell.style.removeProperty('--desktop-compact-surface-left');
        shell.style.removeProperty('--desktop-compact-surface-top');
        shell.style.removeProperty('--desktop-compact-surface-width');
        shell.style.removeProperty('--desktop-compact-surface-height');
        shell.removeAttribute('data-compact-surface-anchor-ready');
        document.documentElement.style.removeProperty('--compact-surface-left');
        document.documentElement.style.removeProperty('--compact-surface-top');
        document.documentElement.style.removeProperty('--compact-surface-width');
        document.documentElement.style.removeProperty('--compact-surface-height');
        document.documentElement.style.removeProperty('--desktop-compact-surface-left');
        document.documentElement.style.removeProperty('--desktop-compact-surface-top');
        document.documentElement.style.removeProperty('--desktop-compact-surface-width');
        document.documentElement.style.removeProperty('--desktop-compact-surface-height');
        document.documentElement.style.removeProperty('--compact-desktop-workarea-width');
        document.documentElement.style.removeProperty('--compact-desktop-workarea-height');
        I.compactSurfaceAnchorSnapshot = '';
        I.compactSurfaceAnchorLocked = false;
        I.dispatchCompactSurfaceLayoutChange(null);
        I.syncCompactInteractionGeometry();
    }

    I.syncCompactSurfaceAnchor = function syncCompactSurfaceAnchor() {
        var shell = I.getShell();
        if (!shell) return;
        if (!I.isCompactHomeMinimizeBallEnabled()) {
            clearCompactSurfaceAnchor();
            return;
        }
        if (I.compactSurfaceAnchorLocked) {
            return;
        }
        if (I.compactSurfaceDesktopResizeActive && I.isElectronChatWindow()) {
            return;
        }
        if (I.compactSurfaceResizeSession && !I.isElectronChatWindow()) {
            return;
        }

        var layoutOverride = I.getElectronCompactLayoutOverride();
        var target = I.getCompactSurfaceTarget(layoutOverride);
        if (!target) {
            clearCompactSurfaceAnchor();
            return;
        }
        var snapshot = [
            Math.round(target.left),
            Math.round(target.top),
            Math.round(target.width),
            Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT),
            (!!(I.dragState && I.dragState.compactSurface) || I.compactSurfaceDesktopDragActive) ? 'dragging' : 'idle'
        ].join(':');
        if (snapshot === I.compactSurfaceAnchorSnapshot) {
            return;
        }

        I.compactSurfaceAnchorSnapshot = snapshot;
        shell.style.setProperty('--compact-surface-left', Math.round(target.left) + 'px');
        shell.style.setProperty('--compact-surface-top', Math.round(target.top) + 'px');
        shell.style.setProperty('--compact-surface-width', Math.round(target.width) + 'px');
        shell.style.setProperty('--compact-surface-height', Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        document.documentElement.style.setProperty('--compact-surface-left', Math.round(target.left) + 'px');
        document.documentElement.style.setProperty('--compact-surface-top', Math.round(target.top) + 'px');
        document.documentElement.style.setProperty('--compact-surface-width', Math.round(target.width) + 'px');
        document.documentElement.style.setProperty('--compact-surface-height', Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        if (layoutOverride && layoutOverride.surface) {
            shell.style.setProperty('--desktop-compact-surface-left', Math.round(target.left) + 'px');
            shell.style.setProperty('--desktop-compact-surface-top', Math.round(target.top) + 'px');
            shell.style.setProperty('--desktop-compact-surface-width', Math.round(target.width) + 'px');
            shell.style.setProperty('--desktop-compact-surface-height', Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-left', Math.round(target.left) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-top', Math.round(target.top) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-width', Math.round(target.width) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-height', Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        }
        if (layoutOverride && layoutOverride.workArea) {
            document.documentElement.style.setProperty('--compact-desktop-workarea-width', Math.round(layoutOverride.workArea.width) + 'px');
            document.documentElement.style.setProperty('--compact-desktop-workarea-height', Math.round(layoutOverride.workArea.height) + 'px');
        } else {
            document.documentElement.style.removeProperty('--compact-desktop-workarea-width');
            document.documentElement.style.removeProperty('--compact-desktop-workarea-height');
        }
        shell.setAttribute('data-compact-surface-anchor-ready', 'true');
        I.dispatchCompactSurfaceLayoutChange({
            left: Math.round(target.left),
            top: Math.round(target.top),
            width: Math.round(target.width),
            height: Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT)
        });
        I.syncCompactInteractionGeometry();
    }

    I.stopCompactMinimizeBallTracking = function stopCompactMinimizeBallTracking() {
        if (I.compactMinimizeBallFrame) {
            window.cancelAnimationFrame(I.compactMinimizeBallFrame);
            I.compactMinimizeBallFrame = 0;
        }
        I.compactSurfaceTrackingSettleFramesRemaining = 0;
        I.compactSurfacePendingModelOpen = false;
        clearCompactSurfaceAnchor();
    }

    function isCompactSurfaceTrackingActive() {
        return !!(
            (I.dragState && I.dragState.compactSurface) ||
            I.compactSurfaceDesktopDragActive ||
            I.compactSurfaceResizeSession ||
            I.compactSurfaceDesktopResizeActive
        );
    }

    I.scheduleCompactMinimizeBallTracking = function scheduleCompactMinimizeBallTracking() {
        if (!I.isCompactHomeMinimizeBallEnabled()) {
            I.stopCompactMinimizeBallTracking();
            return;
        }
        I.compactSurfaceTrackingSettleFramesRemaining = I.COMPACT_SURFACE_IDLE_SETTLE_FRAME_COUNT;
        if (I.compactMinimizeBallFrame) {
            return;
        }

        var loop = function () {
            I.compactMinimizeBallFrame = 0;
            if (!I.isCompactHomeMinimizeBallEnabled()) {
                I.stopCompactMinimizeBallTracking();
                return;
            }
            var trackingActive = isCompactSurfaceTrackingActive();
            if (!trackingActive && I.compactSurfaceTrackingSettleFramesRemaining <= 0) {
                return;
            }
            I.syncCompactSurfaceAnchor();
            I.syncCompactInteractionGeometry();
            if (trackingActive) {
                I.compactSurfaceTrackingSettleFramesRemaining = I.COMPACT_SURFACE_IDLE_SETTLE_FRAME_COUNT;
            } else {
                I.compactSurfaceTrackingSettleFramesRemaining -= 1;
            }
            I.compactMinimizeBallFrame = window.requestAnimationFrame(loop);
        };

        I.syncCompactSurfaceAnchor();
        I.syncCompactInteractionGeometry();
        I.compactMinimizeBallFrame = window.requestAnimationFrame(loop);
    }

    I.revealPendingCompactSurfaceOpen = function revealPendingCompactSurfaceOpen() {
        if (!I.compactSurfacePendingModelOpen) return false;
        if (I.shouldDelayCompactSurfaceOpenForModel()) return false;
        var overlay = I.getOverlay();
        if (!overlay) return false;
        I.compactSurfacePendingModelOpen = false;
        overlay.hidden = false;
        document.body.classList.add('react-chat-window-open');
        I.syncCompactSurfaceAnchor();
        I.scheduleCompactMinimizeBallTracking();
        I.scheduleMobileContentLayout();
        return true;
    }

    function clearMobileExpandVisualGuard() {
        if (I.mobileExpandVisualGuardTimer) {
            window.clearTimeout(I.mobileExpandVisualGuardTimer);
            I.mobileExpandVisualGuardTimer = 0;
        }
        var shell = I.getShell();
        if (shell) {
            shell.classList.remove('is-mobile-expand-guarding');
        }
    }

    I.armMobileExpandClickGuard = function armMobileExpandClickGuard(clientX, clientY) {
        if (!I.isMobileWidth()) return;
        if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
        I.mobileExpandClickGuard = {
            clientX: clientX,
            clientY: clientY,
            expiresAt: Date.now() + I.MOBILE_EXPAND_CLICK_GUARD_MS
        };
        var shell = I.getShell();
        if (shell) {
            shell.classList.add('is-mobile-expand-guarding');
        }
        if (I.mobileExpandVisualGuardTimer) {
            window.clearTimeout(I.mobileExpandVisualGuardTimer);
        }
        I.mobileExpandVisualGuardTimer = window.setTimeout(clearMobileExpandVisualGuard, I.MOBILE_EXPAND_VISUAL_GUARD_MS);
    }

    function shouldBlockMobileExpandClick(event) {
        if (!I.mobileExpandClickGuard) return false;
        var guard = I.mobileExpandClickGuard;
        if (Date.now() > guard.expiresAt) {
            I.mobileExpandClickGuard = null;
            clearMobileExpandVisualGuard();
            return false;
        }
        if (!I.isMobileWidth()) {
            I.mobileExpandClickGuard = null;
            clearMobileExpandVisualGuard();
            return false;
        }
        var dx = event.clientX - guard.clientX;
        var dy = event.clientY - guard.clientY;
        var withinGuardRadius = Math.sqrt(dx * dx + dy * dy) <= I.MOBILE_EXPAND_CLICK_GUARD_RADIUS;
        if (!withinGuardRadius) return false;

        var shell = I.getShell();
        if (shell && !shell.contains(event.target)) return false;
        if (event.type === 'click') {
            I.mobileExpandClickGuard = null;
        }
        return true;
    }

    I.blockMobileExpandSyntheticPointerEvent = function blockMobileExpandSyntheticPointerEvent(event) {
        if (!shouldBlockMobileExpandClick(event)) return;
        // 手机端触摸展开后浏览器会补发同坐标鼠标事件；从 mousedown 起吞掉，避免按钮出现按压反馈。
        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }
    }

    // Web 宿主下用鼠标拖拽 surface 本体后，浏览器仍会在 mouseup 落点补发一次
    // click（mousedown 的 preventDefault 不取消 click）。落点若是胶囊按钮会被误判为
    // 点击而展开输入框。拖拽真正移动过(moved)时 arm 此守卫，吞掉紧随其后的那一次
    // click。click 与 mouseup 同任务同步派发，setTimeout(…,0) 必在其后清旗，既兜底
    // 「落点无 click」也不会误吞之后无关的点击。touch 路径已在 touchstart
    // preventDefault 阶段抑制了合成 click，不经此守卫。
    I.armDragReleaseClickGuard = function armDragReleaseClickGuard() {
        I.suppressDragReleaseClick = true;
        window.setTimeout(function () {
            I.suppressDragReleaseClick = false;
        }, 0);
    }

    I.consumeDragReleaseClickGuard = function consumeDragReleaseClickGuard(event) {
        if (!I.suppressDragReleaseClick) return;
        I.suppressDragReleaseClick = false;
        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }
    }

    I.getOverlay = function getOverlay() {
        return I.$('react-chat-window-overlay');
    }

    I.getShell = function getShell() {
        return I.$('react-chat-window-shell');
    }

    I.getHeader = function getHeader() {
        return I.$('react-chat-window-drag-handle');
    }

    I.isYuiGuideDragLocked = function isYuiGuideDragLocked() {
        var body = document.body;
        if (!body) return false;
        return body.classList.contains('yui-guide-home-ui-suppressed')
            || body.classList.contains('yui-taking-over')
            || body.classList.contains('yui-guide-standalone-input-shield-active')
            || body.classList.contains('yui-guide-chat-buttons-disabled');
    }

    I.getMinimizeButton = function getMinimizeButton() {
        return I.$('reactChatWindowMinimizeButton');
    }

    I.getMinimizeIcon = function getMinimizeIcon() {
        return I.$('reactChatWindowMinimizeIcon');
    }

    I.getRoot = function getRoot() {
        return I.$('react-chat-window-root');
    }

    I.clearMobileContentCap = function clearMobileContentCap() {
        var shell = I.getShell();
        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        if (shell.dataset.mobileAutoHeight !== undefined) {
            shell.style.removeProperty('height');
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function resetMobileContentLayoutState(shell, topbar, composer, messageList) {
        [topbar, composer, messageList].forEach(function (element) {
            if (!element) return;
            element.style.removeProperty('height');
            if (element.dataset && element.dataset.mobileAutoHeight) {
                delete element.dataset.mobileAutoHeight;
            }
        });

        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        shell.style.removeProperty('height');
        if (shell.dataset.mobileAutoHeight) {
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function syncMobileContentLayout() {
        var overlay = I.getOverlay();
        var shell = I.getShell();
        var root = I.getRoot();
        if (!overlay || overlay.hidden || !shell || !root || I.minimized || !I.isMobileWidth()) {
            I.clearMobileContentCap();
            return;
        }

        // 正在拖拽调整高度时不覆盖，等 stopResize() 结束后再同步
        if (I.resizeState) return;

        // 如果用户手动设置了高度，使用用户高度，不自动计算
        if (I.mobileUserHeight > 0) {
            var h = Math.min(I.mobileUserHeight, I.getMobileMaxHeight());
            shell.style.height = h + 'px';
            shell.dataset.mobileAutoHeight = 'false';
            shell.classList.remove('is-mobile-content-capped');
            return;
        }

        var topbar = root.querySelector('.window-topbar');
        var composer = root.querySelector('.composer-panel');
        var messageList = root.querySelector('.message-list');
        var compactStage = root.querySelector('.chat-body-compact-surface');
        var contentNode = messageList;
        if (!contentNode && I.getCurrentChatSurfaceMode() === 'compact') {
            contentNode = compactStage || root.querySelector('.compact-chat-stage') || root.querySelector('.compact-chat-surface-shell');
        }
        if (!topbar || !composer || !contentNode) {
            resetMobileContentLayoutState(shell, topbar, composer, messageList || compactStage);
            return;
        }

        var maxHeight = I.getMobileMaxHeight();
        if (!maxHeight) return;

        var desiredMessageHeight = I.getCurrentChatSurfaceMode() === 'compact'
            ? Math.max(0, Math.ceil(contentNode.getBoundingClientRect().height))
            : Math.max(I.MOBILE_MESSAGE_MIN_HEIGHT, messageList.scrollHeight);
        var desiredHeight = Math.ceil(
            topbar.getBoundingClientRect().height
            + composer.getBoundingClientRect().height
            + desiredMessageHeight
        );
        var nextHeight = Math.min(maxHeight, desiredHeight);

        shell.style.height = nextHeight + 'px';
        shell.dataset.mobileAutoHeight = 'true';
        shell.classList.toggle('is-mobile-content-capped', desiredHeight > maxHeight);
    }

    I.scheduleMobileContentLayout = function scheduleMobileContentLayout() {
        if (I.mobileLayoutFrame) return;

        I.mobileLayoutFrame = window.requestAnimationFrame(function () {
            I.mobileLayoutFrame = 0;
            syncMobileContentLayout();
        });
    }

    I.getI18nText = function getI18nText(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }

        if (typeof window.t === 'function') {
            try {
                var translated = window.t(key, fallback);
                if (translated && translated !== key) {
                    return translated;
                }
            } catch (_) {}
        }

        return fallback;
    }

    function getTextContent(node) {
        return node && node.textContent ? node.textContent.trim() : '';
    }

    function sanitizeDisplayName(value) {
        if (value == null) return '';
        return String(value).trim();
    }

    function getCurrentAssistantName() {
        return sanitizeDisplayName(
            window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__
            || (window.lanlan_config && window.lanlan_config.lanlan_name)
            || window._currentCatgirl
            || window.currentCatgirl
        ) || 'Neko';
    }

    I.getCurrentUserName = function getCurrentUserName() {
        var candidates = [
            window.master_display_name,
            window.lanlan_config && window.lanlan_config.master_display_name,
            window.master_nickname,
            window.lanlan_config && window.lanlan_config.master_nickname,
            window.master_name,
            window.lanlan_config && window.lanlan_config.master_name,
            window.currentUser && (window.currentUser.nickname || window.currentUser.display_name || window.currentUser.displayName || window.currentUser.username || window.currentUser.name),
            window.userProfile && (window.userProfile.nickname || window.userProfile.display_name || window.userProfile.displayName || window.userProfile.username || window.userProfile.name),
            window.appUser && (window.appUser.nickname || window.appUser.display_name || window.appUser.displayName || window.appUser.username || window.appUser.name),
            window.username,
            window.userName,
            window.displayName,
            window.nickname
        ];

        for (var i = 0; i < candidates.length; i += 1) {
            var resolved = sanitizeDisplayName(candidates[i]);
            if (resolved) return resolved;
        }

        try {
            var storageKeys = ['nickname', 'displayName', 'userName', 'username'];
            for (var j = 0; j < storageKeys.length; j += 1) {
                var stored = sanitizeDisplayName(localStorage.getItem(storageKeys[j]));
                if (stored) return stored;
            }
        } catch (_) {}

        return 'You';
    }

    function getDefaultAuthorByRole(role) {
        return role === 'user' ? I.getCurrentUserName() : getCurrentAssistantName();
    }

    I.createBaseViewProps = function createBaseViewProps() {
        var titleNode = I.$('chat-title');
        var textSendButton = I.$('textSendButton');
        var sendButtonLabelNode = textSendButton ? textSendButton.querySelector('[data-i18n="chat.send"]') : null;
        var title = getTextContent(titleNode)
            || I.getI18nText('chat.title', '对话')
            || '对话';
        var inputPlaceholder = I.getI18nText('chat.textInputPlaceholderCompact', '')
            || I.getI18nText('chat.textInputPlaceholderShort', '')
            || I.getI18nText('chat.textInputPlaceholder', '')
            || '输入消息...';
        var sendButtonLabel = getTextContent(sendButtonLabelNode)
            || I.getI18nText('chat.send', '发送')
            || '发送';

        return {
            title: title,
            iconSrc: '/static/icons/chat_icon.png',
            inputPlaceholder: inputPlaceholder,
            sendButtonLabel: sendButtonLabel,
            emptyText: I.getI18nText('chat.emptyState', '聊天内容接入后会显示在这里。'),
            chatWindowAriaLabel: I.getI18nText('chat.reactWindowAriaLabel', 'Neko chat window'),
            messageListAriaLabel: I.getI18nText('chat.messageListAriaLabel', 'Chat messages'),
            composerToolsAriaLabel: I.getI18nText('chat.composerToolsAriaLabel', 'Composer tools'),
            composerAttachmentsAriaLabel: I.getI18nText('chat.pendingImagesAriaLabel', 'Pending attachments'),
            importImageButtonLabel: I.getI18nText('chat.importImage', '导入图片'),
            screenshotButtonLabel: I.isMobileWidth()
                ? I.getI18nText('chat.takePhoto', '拍照')
                : I.getI18nText('chat.screenshot', '截图'),
            importImageButtonAriaLabel: I.getI18nText('chat.importImageAriaLabel', '导入图片'),
            screenshotButtonAriaLabel: I.isMobileWidth()
                ? I.getI18nText('chat.takePhotoAriaLabel', '拍照')
                : I.getI18nText('chat.screenshotAriaLabel', '截图'),
            removeAttachmentButtonAriaLabel: I.getI18nText('chat.removePendingImage', '移除图片'),
            failedStatusLabel: I.getI18nText('chat.messageFailed', '发送失败'),
            inputHint: I.getI18nText('chat.reactWindowInputHint', 'Enter 发送，Shift + Enter 换行'),
            jukeboxButtonLabel: I.getI18nText('chat.jukeboxLabel', '点歌台'),
            jukeboxButtonAriaLabel: I.getI18nText('chat.jukebox', '点歌台'),
            avatarGeneratorButtonLabel: I.getI18nText('chat.avatarPreviewLabel', '头像'),
            avatarGeneratorButtonAriaLabel: I.getI18nText('chat.avatarPreview', '生成头像'),
            exportConversationButtonLabel: I.getI18nText('chat.exportConversation', '导出对话'),
            exportConversationButtonAriaLabel: I.getI18nText('chat.exportConversation', '导出对话'),
            chatSurfaceMode: I.getCurrentChatSurfaceMode(),
            compactChatState: I.getCurrentCompactChatState(),
            translateEnabled: (window.appState && typeof window.appState.subtitleEnabled !== 'undefined')
                ? !!window.appState.subtitleEnabled
                : localStorage.getItem('subtitleEnabled') === 'true',
            translateButtonLabel: I.getI18nText('subtitle.enable', '字幕翻译'),
            translateButtonAriaLabel: I.getI18nText('subtitle.enableAriaLabel', '字幕翻译开关'),
            galgameToggleButtonLabel: I.getI18nText('chat.galgameToggle', 'GalGame 模式'),
            galgameToggleButtonAriaLabel: I.getI18nText('chat.galgameToggleAriaLabel', '切换 GalGame 选项模式'),
            galgameLoadingLabel: I.getI18nText('chat.galgameLoading', '生成回复选项中…'),
            composerDisabled: !!I.state.homeTutorialInteractionLocked,
            compactInputLocked: !!I.state.homeTutorialInputLocked
        };
    }

    I.ensureViewProps = function ensureViewProps() {
        if (!I.state.viewProps) {
            I.state.viewProps = I.createBaseViewProps();
        }
        return I.state.viewProps;
    }

    function createTutorialChatRequest(payload) {
        I.tutorialChatRequestSeq += 1;
        return Object.assign({
            id: 'tutorial-chat-' + Date.now() + '-' + I.tutorialChatRequestSeq
        }, payload || {});
    }

    I.setTutorialChatRequest = function setTutorialChatRequest(propName, payload) {
        I.state.viewProps = Object.assign({}, I.ensureViewProps(), {
            [propName]: createTutorialChatRequest(payload)
        });
        I.renderWindow();
        return true;
    }

    I.cloneMessage = function cloneMessage(message) {
        if (!message || typeof message !== 'object') return null;
        return {
            id: message.id,
            role: message.role,
            author: message.author,
            time: message.time,
            createdAt: message.createdAt,
            turnId: message.turnId,
            avatarLabel: message.avatarLabel,
            baseAvatarUrl: message.baseAvatarUrl || message.avatarUrl,
            avatarUrl: message.avatarUrl,
            blocks: Array.isArray(message.blocks) ? message.blocks.map(function (block) {
                if (!block || typeof block !== 'object') return null;
                if (block.type === 'buttons' && Array.isArray(block.buttons)) {
                    return {
                        type: 'buttons',
                        buttons: block.buttons.map(function (button) {
                            if (!button || typeof button !== 'object') return null;
                            return {
                                id: button.id,
                                label: button.label,
                                action: button.action,
                                variant: button.variant,
                                disabled: !!button.disabled,
                                payload: button.payload || undefined
                            };
                        }).filter(Boolean)
                    };
                }
                return Object.assign({}, block);
            }).filter(Boolean) : [],
            actions: Array.isArray(message.actions) ? message.actions.map(function (action) {
                if (!action || typeof action !== 'object') return null;
                return {
                    id: action.id,
                    label: action.label,
                    action: action.action,
                    variant: action.variant,
                    disabled: !!action.disabled,
                    payload: action.payload || undefined
                };
            }).filter(Boolean) : undefined,
            status: message.status,
            sortKey: message.sortKey
        };
    }

    I.normalizeMessage = function normalizeMessage(rawMessage, fallbackSortKey) {
        var message = I.cloneMessage(rawMessage);
        if (!message || !message.id) return null;

        var now = Date.now();
        var createdAt = typeof message.createdAt === 'number' ? message.createdAt : now;
        var time = message.time;
        if (!time) {
            try {
                time = new Date(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } catch (_) {
                time = '';
            }
        }

        var baseAvatarUrl = message.baseAvatarUrl || message.avatarUrl;
        return {
            id: String(message.id),
            role: message.role || 'assistant',
            author: sanitizeDisplayName(message.author) || getDefaultAuthorByRole(message.role || 'assistant'),
            time: time,
            createdAt: createdAt,
            turnId: message.turnId ? String(message.turnId) : undefined,
            avatarLabel: message.avatarLabel,
            baseAvatarUrl: baseAvatarUrl,
            avatarUrl: resolveCurrentAssistantAvatarUrl(message.role, baseAvatarUrl),
            blocks: Array.isArray(message.blocks) ? message.blocks : [],
            actions: Array.isArray(message.actions) ? message.actions : undefined,
            status: message.status,
            sortKey: typeof message.sortKey === 'number' ? message.sortKey : fallbackSortKey
        };
    }

    function resolveCurrentAssistantAvatarUrl(role, baseAvatarUrl) {
        if (role !== 'assistant') return baseAvatarUrl || undefined;
        try {
            if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                return window.appChatAvatar.getCurrentAvatarDataUrl() || baseAvatarUrl || undefined;
            }
        } catch (_) {}
        return baseAvatarUrl || undefined;
    }

    I.refreshAssistantAvatarUrls = function refreshAssistantAvatarUrls(event) {
        var changed = false;
        I.state.messages = I.state.messages.map(function (message) {
            if (!message || message.role !== 'assistant') return message;
            var baseAvatarUrl = message.baseAvatarUrl || message.avatarUrl || '';
            var avatarUrl = resolveCurrentAssistantAvatarUrl('assistant', baseAvatarUrl);
            if (message.avatarUrl === avatarUrl && message.baseAvatarUrl === baseAvatarUrl) return message;
            changed = true;
            return Object.assign({}, message, {
                baseAvatarUrl: baseAvatarUrl,
                avatarUrl: avatarUrl
            });
        });
        if (changed) I.renderWindow();
    }

    I.sortMessages = function sortMessages(messages) {
        return messages.slice().sort(function (a, b) {
            var sortA = typeof a.sortKey === 'number' ? a.sortKey : (typeof a.createdAt === 'number' ? a.createdAt : 0);
            var sortB = typeof b.sortKey === 'number' ? b.sortKey : (typeof b.createdAt === 'number' ? b.createdAt : 0);
            if (sortA !== sortB) return sortA - sortB;
            return String(a.id).localeCompare(String(b.id));
        });
    }

    I.buildRenderProps = function buildRenderProps() {
        if (I.state.rollbackDraft) {
            console.log('[ROLLBACK] buildRenderProps: rollbackDraftPresent=true length=' + I.state.rollbackDraft.length + ' key=' + I.state._rollbackKey);
        }
        return Object.assign({}, I.ensureViewProps(), {
            messages: I.state.messages,
            composerAttachments: I.state.composerAttachments,
            rollbackDraft: I.state.rollbackDraft || undefined,
            _rollbackKey: I.state._rollbackKey || undefined,
            _avatarToolDeactivationKey: I.state._avatarToolDeactivationKey || undefined,
            composerHidden: I.getEffectiveComposerHidden(),
            chatSurfaceMode: I.getCurrentChatSurfaceMode(),
            compactMinimizeCancelSeq: I.compactMinimizeCancelSeq,
            compactChatState: I.getCurrentCompactChatState(),
            galgameModeEnabled: !!I.state.galgameModeEnabled,
            galgameOptions: Array.isArray(I.state.galgameOptions) ? I.state.galgameOptions : [],
            galgameOptionsLoading: !!I.state.galgameOptionsLoading,
            choicePrompt: I.getRevealedChoicePrompt(),
            onMessageAction: I.handleMessageAction,
            onComposerImportImage: I.handleComposerImportImage,
            onComposerScreenshot: I.handleComposerScreenshot,
            onComposerRemoveAttachment: I.handleComposerRemoveAttachment,
            onComposerSubmit: I.handleComposerSubmit,
            onAvatarInteraction: I.handleAvatarInteraction,
            onAvatarToolStateChange: I.handleAvatarToolStateChange,
            onJukeboxClick: I.handleJukeboxClick,
            onAvatarGeneratorClick: I.handleAvatarGeneratorClick,
            onExportConversationClick: I.handleExportConversationClick,
            onTranslateToggle: I.handleTranslateToggle,
            onGalgameModeToggle: I.handleGalgameModeToggle,
            onGalgameOptionSelect: I.handleGalgameOptionSelect,
            onChoiceSelect: I.handleChoiceSelect,
            onCompactChatStateChange: I.handleCompactChatStateChange,
            onCompactMinimizeRequest: I.handleCompactMinimizeRequest,
            avatarToolMenuOpenRequest: I.state.viewProps.avatarToolMenuOpenRequest || null,
            compactToolFanOpenRequest: I.state.viewProps.compactToolFanOpenRequest || null,
            compactToolWheelRotateRequest: I.state.viewProps.compactToolWheelRotateRequest || null,
            compactToolWheelIndexRequest: I.state.viewProps.compactToolWheelIndexRequest || null,
            compactHistoryOpenRequest: I.state.viewProps.compactHistoryOpenRequest || null
        });
    }

    I.showToast = function showToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration || 3000);
        }
    }

})();
