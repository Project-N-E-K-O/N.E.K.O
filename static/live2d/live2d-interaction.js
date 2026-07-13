/**
 * Live2D Interaction - 拖拽、缩放、鼠标跟踪等交互功能
 */

// ===== 自动吸附功能配置 =====
const SNAP_CONFIG = {
    // 吸附阈值：模型在屏幕内剩余的像素小于此值时触发吸附（即模型绝大部分超出屏幕）
    threshold: 200,
    // 吸附边距：吸附后距离屏幕边缘的最小距离
    margin: 5,
    // 动画持续时间（毫秒）
    animationDuration: 260,
    // 动画缓动函数类型
    easingType: 'easeOutBack'
};

// ===== 缩放限制配置 =====
const SCALE_LIMITS = {
    MIN: 0.005, // 最小缩放比例
    MAX: 5.0     // 最大缩放比例（暂不实施，保留供后续使用）
};

// 缓动函数集合
const EasingFunctions = {
    // 线性
    linear: t => t,
    // 缓出二次方
    easeOutQuad: t => t * (2 - t),
    // 缓出三次方（更自然）
    easeOutCubic: t => (--t) * t * t + 1,
    // 缓出回弹（与聊天框一致）
    easeOutBack: t => {
        const c1 = 1.70158;
        const c3 = c1 + 1;
        return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
    },
    // 缓出弹性
    easeOutElastic: t => {
        const p = 0.3;
        return Math.pow(2, -10 * t) * Math.sin((t - p / 4) * (2 * Math.PI) / p) + 1;
    },
    // 缓入缓出
    easeInOutQuad: t => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t
};

function getLive2DNiriPetPhysicalCropApi() {
    const api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
    if (!api || typeof api !== 'object') return null;
    try {
        if (typeof api.isActive === 'function' && !api.isActive()) return null;
    } catch (_) {
        return null;
    }
    return api;
}

function normalizeLive2DPoint(point) {
    if (!point || typeof point !== 'object') return null;
    const x = Number(point.x);
    const y = Number(point.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
}

function logLive2DClickTriggerSummary(label, details = {}) {
    const motions = Array.isArray(details.motions) ? details.motions.filter(Boolean) : [];
    const expressions = Array.isArray(details.expressions) ? details.expressions.filter(Boolean) : [];
    const failedMotions = Array.isArray(details.failedMotions) ? details.failedMotions.filter(Boolean) : [];
    const failedExpressions = Array.isArray(details.failedExpressions) ? details.failedExpressions.filter(Boolean) : [];
    const motionCount = motions.length;
    const expressionCount = expressions.length;
    const triggerCount = motionCount + expressionCount;
    console.log(`[${label}] click trigger summary: triggered=${triggerCount}, motions=${motionCount}, expressions=${expressionCount}`, {
        requestedHitArea: details.requestedHitArea || null,
        resolvedHitArea: details.resolvedHitArea || null,
        fallback: details.fallback || null,
        reason: details.reason || null,
        summaryType: details.summaryType || 'trigger_result',
        emotion: details.emotion || null,
        priority: details.priority ?? null,
        durationMs: details.durationMs ?? null,
        motionCandidates: Number.isFinite(details.motionCandidates) ? details.motionCandidates : 0,
        expressionCandidates: Number.isFinite(details.expressionCandidates) ? details.expressionCandidates : 0,
        motions,
        expressions,
        failedMotions,
        failedExpressions
    });
}

function getLive2DNiriPetPointerCoordinates(event) {
    const raw = {
        x: Number(event && event.clientX),
        y: Number(event && event.clientY)
    };
    if (!Number.isFinite(raw.x) || !Number.isFinite(raw.y)) {
        return {
            local: { x: 0, y: 0 },
            virtual: { x: 0, y: 0 },
            active: false,
            patched: false
        };
    }

    const api = getLive2DNiriPetPhysicalCropApi();
    if (api && typeof api.getEventCoordinates === 'function') {
        try {
            const coords = api.getEventCoordinates(event);
            const local = normalizeLive2DPoint(coords && coords.local);
            const virtual = normalizeLive2DPoint(coords && coords.virtual);
            if (local && virtual) {
                return {
                    local,
                    virtual,
                    active: coords.active === true,
                    patched: coords.patched === true
                };
            }
        } catch (_) {}
    }

    return {
        local: raw,
        virtual: raw,
        active: false,
        patched: false
    };
}

function isLive2DPointInRect(point, rect, padding = 0) {
    const p = normalizeLive2DPoint(point);
    if (!p || !rect) return false;
    const pad = Number.isFinite(Number(padding)) ? Number(padding) : 0;
    return p.x >= rect.left - pad &&
        p.x <= rect.right + pad &&
        p.y >= rect.top - pad &&
        p.y <= rect.bottom + pad;
}

const LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO = 0.025;
const LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MIN_PX = 12;
const LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MAX_PX = 24;
const LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_RATIO = 0.22;
const LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MIN_PX = 96;
const LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MAX_PX = 180;
const LIVE2D_GAME_MODE_EDGE_PEEK_SIDE_ROTATION_DEGREES = 60;
const LIVE2D_GAME_MODE_EDGE_PEEK_CORNER_ROTATION_DEGREES = 45;
const LIVE2D_GAME_MODE_EDGE_PEEK_TOP_CORNER_ROTATION_DEGREES = 135;
const LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO = 0.24;
const LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MARGIN_PX = 8;
const LIVE2D_GAME_MODE_EDGE_PEEK_ANIMATION_MS = 200;
let live2DGameModeEdgePeekDisplayContext = null;
let live2DGameModeEdgePeekDisplayRefresh = null;

function isLive2DGameModeEdgePeekMacRuntime() {
    try {
        return !!(window.__NEKO_DESKTOP_RUNTIME__ &&
            window.__NEKO_DESKTOP_RUNTIME__.platform === 'darwin');
    } catch (_) {
        return false;
    }
}

function isLive2DGameModeEdgePeekDesktopRuntime() {
    try {
        return isLive2DGameModeEdgePeekMacRuntime() || !!(
            window.electronScreen &&
            typeof window.electronScreen.getCurrentDisplay === 'function'
        );
    } catch (_) {
        return false;
    }
}

function normalizeLive2DGameModeEdgePeekRect(rect) {
    if (!rect) return null;
    const x = Number(rect.x);
    const y = Number(rect.y);
    const width = Number(rect.width);
    const height = Number(rect.height);
    if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return { x, y, width, height };
}

function refreshLive2DGameModeEdgePeekDisplayContext(force = false) {
    if (!isLive2DGameModeEdgePeekDesktopRuntime()) {
        live2DGameModeEdgePeekDisplayContext = null;
        return Promise.resolve(null);
    }
    const electronScreen = window.electronScreen;
    if (!electronScreen || typeof electronScreen.getCurrentDisplay !== 'function') {
        live2DGameModeEdgePeekDisplayContext = null;
        return Promise.resolve(null);
    }
    if (!force && live2DGameModeEdgePeekDisplayContext) {
        return Promise.resolve(live2DGameModeEdgePeekDisplayContext);
    }
    if (live2DGameModeEdgePeekDisplayRefresh) {
        return live2DGameModeEdgePeekDisplayRefresh;
    }

    live2DGameModeEdgePeekDisplayRefresh = Promise.resolve()
        .then(() => electronScreen.getCurrentDisplay())
        .then((displayInfo) => {
            const workArea = normalizeLive2DGameModeEdgePeekRect(displayInfo && displayInfo.workArea);
            const displayBounds = normalizeLive2DGameModeEdgePeekRect(displayInfo && displayInfo.bounds);
            const screenX = Number.isFinite(Number(displayInfo && displayInfo.screenX))
                ? Number(displayInfo.screenX)
                : (displayBounds ? Number(displayBounds.x) : NaN);
            const screenY = Number.isFinite(Number(displayInfo && displayInfo.screenY))
                ? Number(displayInfo.screenY)
                : (displayBounds ? Number(displayBounds.y) : NaN);
            live2DGameModeEdgePeekDisplayContext = workArea &&
                Number.isFinite(screenX) && Number.isFinite(screenY)
                ? { screenX, screenY, workArea }
                : null;
            return live2DGameModeEdgePeekDisplayContext;
        })
        .catch(() => {
            live2DGameModeEdgePeekDisplayContext = null;
            return null;
        })
        .finally(() => {
            live2DGameModeEdgePeekDisplayRefresh = null;
        });
    return live2DGameModeEdgePeekDisplayRefresh;
}

function getLive2DGameModeEdgePeekTriggerViewport(viewport) {
    const context = isLive2DGameModeEdgePeekDesktopRuntime()
        ? live2DGameModeEdgePeekDisplayContext
        : null;
    if (!viewport || !context || !context.workArea) return viewport;

    const area = context.workArea;
    const left = clampLive2DGameModeEdgePeekCoordinate(
        area.x - context.screenX,
        viewport.left,
        viewport.right
    );
    const top = clampLive2DGameModeEdgePeekCoordinate(
        area.y - context.screenY,
        viewport.top,
        viewport.bottom
    );
    const right = clampLive2DGameModeEdgePeekCoordinate(
        area.x + area.width - context.screenX,
        viewport.left,
        viewport.right
    );
    const bottom = clampLive2DGameModeEdgePeekCoordinate(
        area.y + area.height - context.screenY,
        viewport.top,
        viewport.bottom
    );
    if (right <= left || bottom <= top) return viewport;
    return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top
    };
}

function isLive2DGameModeEdgePeekEnabled() {
    try {
        return !!(window.nekoGameModeBeta &&
            typeof window.nekoGameModeBeta.isEnabled === 'function' &&
            window.nekoGameModeBeta.isEnabled());
    } catch (_) {
        return false;
    }
}

function getLive2DGameModeEdgePeekBounds(model) {
    if (!model || typeof model.getBounds !== 'function') return null;
    let bounds = null;
    try {
        bounds = model.getBounds();
    } catch (_) {
        return null;
    }
    if (!bounds) return null;
    const left = Number.isFinite(bounds.left) ? bounds.left : bounds.x;
    const top = Number.isFinite(bounds.top) ? bounds.top : bounds.y;
    const right = Number.isFinite(bounds.right) ? bounds.right : left + bounds.width;
    const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : top + bounds.height;
    const width = Number.isFinite(bounds.width) ? bounds.width : right - left;
    const height = Number.isFinite(bounds.height) ? bounds.height : bottom - top;
    if (![left, top, right, bottom, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return { left, top, right, bottom, width, height };
}

function clampLive2DGameModeEdgePeekCoordinate(value, min, max) {
    if (!Number.isFinite(value)) return min;
    return Math.min(max, Math.max(min, value));
}

function getLive2DGameModeEdgePeekViewport(bounds = null, manager = null) {
    const fallbackW = bounds && Number.isFinite(bounds.width) ? bounds.width : 1;
    const fallbackH = bounds && Number.isFinite(bounds.height) ? bounds.height : 1;
    const renderer = manager && manager.pixi_app && manager.pixi_app.renderer;
    const screen = renderer && renderer.screen;
    const rendererW = Number(screen && screen.width);
    const rendererH = Number(screen && screen.height);
    const viewportW = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : Number(window.innerWidth);
    const viewportH = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : Number(window.innerHeight);
    const width = Number.isFinite(viewportW) && viewportW > 0 ? viewportW : fallbackW;
    const height = Number.isFinite(viewportH) && viewportH > 0 ? viewportH : fallbackH;
    return { left: 0, top: 0, right: width, bottom: height, width, height };
}

function getLive2DGameModeEdgePeekViewportIntersection(bounds, viewport) {
    if (!bounds || !viewport) return null;
    const left = Math.max(bounds.left, viewport.left);
    const right = Math.min(bounds.right, viewport.right);
    const top = Math.max(bounds.top, viewport.top);
    const bottom = Math.min(bounds.bottom, viewport.bottom);
    const width = right - left;
    const height = bottom - top;
    if (![left, right, top, bottom, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return {
        left,
        right,
        top,
        bottom,
        width,
        height,
        centerX: left + width / 2,
        centerY: top + height / 2
    };
}

function getLive2DGameModeEdgePeekAnchor(bounds, viewport) {
    if (!bounds || !viewport) return null;
    const horizontalThreshold = clampLive2DGameModeEdgePeekCoordinate(
        bounds.width * LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO,
        LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MIN_PX,
        LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MAX_PX
    );
    const verticalThreshold = clampLive2DGameModeEdgePeekCoordinate(
        bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO,
        LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MIN_PX,
        LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_MAX_PX
    );
    const nearLeft = bounds.left <= viewport.left + horizontalThreshold;
    const nearRight = viewport.right - bounds.right <= horizontalThreshold;
    if (!nearLeft && !nearRight) return null;
    const nearTop = bounds.top <= viewport.top + verticalThreshold;
    const nearBottom = viewport.bottom - bounds.bottom <= verticalThreshold;
    const side = nearLeft && nearRight
        ? (bounds.left <= viewport.right - bounds.right ? 'left' : 'right')
        : (nearLeft ? 'left' : 'right');
    let verticalEdge = '';
    if (nearTop && nearBottom) {
        verticalEdge = bounds.top + bounds.height / 2 <= viewport.top + viewport.height / 2
            ? 'top'
            : 'bottom';
    } else if (nearTop) {
        verticalEdge = 'top';
    } else if (nearBottom) {
        verticalEdge = 'bottom';
    }
    return {
        edge: verticalEdge ? `${verticalEdge}-${side}` : side,
        side,
        verticalEdge
    };
}

function getLive2DGameModeEdgePeekRotationDegrees(anchor) {
    if (!anchor || !anchor.side) return 0;
    if (!anchor.verticalEdge) {
        return anchor.side === 'left'
            ? LIVE2D_GAME_MODE_EDGE_PEEK_SIDE_ROTATION_DEGREES
            : -LIVE2D_GAME_MODE_EDGE_PEEK_SIDE_ROTATION_DEGREES;
    }
    if (anchor.verticalEdge === 'top') {
        return anchor.side === 'left'
            ? LIVE2D_GAME_MODE_EDGE_PEEK_TOP_CORNER_ROTATION_DEGREES
            : -LIVE2D_GAME_MODE_EDGE_PEEK_TOP_CORNER_ROTATION_DEGREES;
    }
    return anchor.side === 'left'
        ? LIVE2D_GAME_MODE_EDGE_PEEK_CORNER_ROTATION_DEGREES
        : -LIVE2D_GAME_MODE_EDGE_PEEK_CORNER_ROTATION_DEGREES;
}

function getLive2DGameModeEdgePeekRevealWidth(bounds) {
    if (!bounds) return LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MIN_PX;
    const width = clampLive2DGameModeEdgePeekCoordinate(
        bounds.width * LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_RATIO,
        LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MIN_PX,
        LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MAX_PX
    );
    return Math.min(bounds.width, width);
}

function getLive2DGameModeEdgePeekHeadAnchor(manager) {
    if (!manager || typeof manager.getHeadScreenAnchor !== 'function') return null;
    try {
        const anchor = manager.getHeadScreenAnchor();
        const x = Number(anchor && anchor.x);
        const y = Number(anchor && anchor.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DGameModeEdgePeekBodyRect(manager) {
    if (!manager || typeof manager.getBodyScreenRectInfo !== 'function') return null;
    try {
        const info = manager.getBodyScreenRectInfo();
        const rect = info && info.rect;
        const centerX = Number(rect && (Number.isFinite(rect.centerX)
            ? rect.centerX
            : Number(rect.left) + Number(rect.width) * 0.5));
        const bottom = Number(rect && rect.bottom);
        return Number.isFinite(centerX) && Number.isFinite(bottom) ? { centerX, bottom } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DGameModeEdgePeekFallbackHeadLocalPoint(model, bounds) {
    if (!model || !bounds || typeof model.toLocal !== 'function' || typeof model.toGlobal !== 'function') {
        return null;
    }
    try {
        const localPoint = model.toLocal({
            x: bounds.left + bounds.width * 0.5,
            y: bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO
        });
        const x = Number(localPoint && localPoint.x);
        const y = Number(localPoint && localPoint.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DGameModeEdgePeekGlobalPoint(model, localPoint) {
    if (!model || !localPoint || typeof model.toGlobal !== 'function') return null;
    try {
        const point = model.toGlobal(localPoint);
        const x = Number(point && point.x);
        const y = Number(point && point.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DGameModeEdgePeekInwardScaleX(model, side) {
    const rawScaleX = model && model.scale && Number.isFinite(Number(model.scale.x))
        ? Number(model.scale.x)
        : 1;
    const baseScaleX = rawScaleX === 0 ? 1 : rawScaleX;
    return side === 'left' ? Math.abs(baseScaleX) : -Math.abs(baseScaleX);
}

function getLive2DGameModeEdgePeekVerticalCorrection(bounds, viewport) {
    if (!bounds || !viewport) return 0;
    const margin = LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MARGIN_PX;
    if (bounds.bottom < viewport.top + margin) return viewport.top + margin - bounds.bottom;
    if (bounds.top > viewport.bottom - margin) return viewport.bottom - margin - bounds.top;
    return 0;
}

function getLive2DGameModeEdgePeekPlacement(model, bounds, manager = null) {
    if (!model || !bounds) return null;
    const viewport = getLive2DGameModeEdgePeekViewport(bounds, manager);
    const triggerViewport = getLive2DGameModeEdgePeekTriggerViewport(viewport);
    const anchor = getLive2DGameModeEdgePeekAnchor(bounds, triggerViewport);
    if (!anchor) return null;
    const { edge, side, verticalEdge } = anchor;

    const baseX = Number(model.x) || 0;
    const baseY = Number(model.y) || 0;
    const baseRotation = Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0;
    const baseScaleX = model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1;
    const targetRotationDegrees = getLive2DGameModeEdgePeekRotationDegrees(anchor);
    const targetRotation = targetRotationDegrees * Math.PI / 180;
    const targetScaleX = getLive2DGameModeEdgePeekInwardScaleX(model, side);
    const baseHeadAnchor = getLive2DGameModeEdgePeekHeadAnchor(manager);
    const fallbackHeadLocalPoint = baseHeadAnchor
        ? null
        : getLive2DGameModeEdgePeekFallbackHeadLocalPoint(model, bounds);
    const fallbackBaseHeadAnchor = getLive2DGameModeEdgePeekGlobalPoint(model, fallbackHeadLocalPoint);
    const effectiveBaseHeadAnchor = baseHeadAnchor || fallbackBaseHeadAnchor;
    const baseBodyRect = getLive2DGameModeEdgePeekBodyRect(manager);
    const baseHeadY = effectiveBaseHeadAnchor
        ? effectiveBaseHeadAnchor.y
        : bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO;
    const desiredHeadY = clampLive2DGameModeEdgePeekCoordinate(
        baseHeadY,
        viewport.top + LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MARGIN_PX,
        viewport.bottom - LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MARGIN_PX
    );

    let transformedBounds = null;
    let transformedHeadAnchor = null;
    let transformedHeadAnchorSource = '';
    let transformedBodyRect = null;
    try {
        model.x = baseX;
        model.y = baseY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        transformedBounds = getLive2DGameModeEdgePeekBounds(model);
        transformedHeadAnchor = getLive2DGameModeEdgePeekHeadAnchor(manager);
        if (transformedHeadAnchor) {
            transformedHeadAnchorSource = 'manager';
        } else {
            transformedHeadAnchor = getLive2DGameModeEdgePeekGlobalPoint(model, fallbackHeadLocalPoint);
            if (transformedHeadAnchor) transformedHeadAnchorSource = 'bounds-fallback';
        }
        transformedBodyRect = getLive2DGameModeEdgePeekBodyRect(manager);
    } catch (_) {
        transformedBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    if (!transformedBounds) return null;

    const revealWidth = getLive2DGameModeEdgePeekRevealWidth(transformedBounds);
    const headInset = clampLive2DGameModeEdgePeekCoordinate(revealWidth * 0.42, 48, 84);
    const desiredHeadX = side === 'left'
        ? viewport.left + headInset
        : viewport.right - headInset;
    const useHeadAnchor = !!verticalEdge && !!transformedHeadAnchor;
    const useWaistAnchor = !verticalEdge && !!(baseBodyRect && transformedBodyRect);
    const desiredWaistX = side === 'left' ? viewport.left - 8 : viewport.right + 8;
    let offsetX = useHeadAnchor
        ? desiredHeadX - transformedHeadAnchor.x
        : (useWaistAnchor
        ? desiredWaistX - transformedBodyRect.centerX
        : (transformedHeadAnchor
            ? desiredHeadX - transformedHeadAnchor.x
            : (side === 'left'
            ? viewport.left + revealWidth - transformedBounds.right
            : viewport.right - revealWidth - transformedBounds.left)));
    const targetHeadY = transformedHeadAnchor
        ? transformedHeadAnchor.y
        : transformedBounds.top + transformedBounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO;
    let offsetY;
    if (useHeadAnchor) {
        const desiredHeadInsetY = clampLive2DGameModeEdgePeekCoordinate(revealWidth * 0.32, 36, 64);
        const desiredHeadYAtEdge = verticalEdge === 'bottom'
            ? viewport.bottom - desiredHeadInsetY
            : viewport.top + desiredHeadInsetY;
        offsetY = desiredHeadYAtEdge - transformedHeadAnchor.y;
    } else if (useWaistAnchor) {
        offsetY = baseBodyRect.bottom - transformedBodyRect.bottom;
    } else if (verticalEdge === 'top') {
        offsetY = viewport.top + revealWidth - transformedBounds.bottom;
    } else if (verticalEdge === 'bottom') {
        offsetY = viewport.bottom - revealWidth - transformedBounds.top;
    } else {
        offsetY = desiredHeadY - targetHeadY;
    }

    let targetBounds = null;
    try {
        model.x = baseX + offsetX;
        model.y = baseY + offsetY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        targetBounds = getLive2DGameModeEdgePeekBounds(model);
    } catch (_) {
        targetBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    if (!targetBounds) return null;
    offsetY += getLive2DGameModeEdgePeekVerticalCorrection(targetBounds, viewport);

    try {
        model.x = baseX + offsetX;
        model.y = baseY + offsetY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        targetBounds = getLive2DGameModeEdgePeekBounds(model);
    } catch (_) {
        targetBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    const visibleBounds = getLive2DGameModeEdgePeekViewportIntersection(targetBounds, viewport);
    if (!visibleBounds) return null;

    return {
        edge,
        side,
        x: baseX + offsetX,
        y: baseY + offsetY,
        rotation: targetRotation,
        rotationDegrees: targetRotationDegrees,
        scaleX: targetScaleX,
        headAnchored: useHeadAnchor || !!transformedHeadAnchor,
        headAnchorSource: transformedHeadAnchorSource,
        waistAnchored: useWaistAnchor,
        revealWidth,
        visibleBounds
    };
}

function animateLive2DGameModeEdgePeekTransform(model, target, duration = LIVE2D_GAME_MODE_EDGE_PEEK_ANIMATION_MS, shouldContinue = null) {
    return new Promise((resolve) => {
        if (!model || model.destroyed || !target) {
            resolve(false);
            return;
        }
        const start = {
            x: Number(model.x) || 0,
            y: Number(model.y) || 0,
            rotation: Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0,
            scaleX: model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1
        };
        const startTime = performance.now();
        const total = Math.max(0, Number(duration) || 0);
        const apply = (progress) => {
            const eased = EasingFunctions.easeOutCubic(progress);
            model.x = start.x + (target.x - start.x) * eased;
            model.y = start.y + (target.y - start.y) * eased;
            model.rotation = start.rotation + (target.rotation - start.rotation) * eased;
            if (model.scale) {
                model.scale.x = start.scaleX + (target.scaleX - start.scaleX) * eased;
            }
        };
        if (total <= 0) {
            if (typeof shouldContinue === 'function' && !shouldContinue()) {
                resolve(false);
                return;
            }
            apply(1);
            resolve(true);
            return;
        }
        const step = (currentTime) => {
            if (!model || model.destroyed) {
                resolve(false);
                return;
            }
            if (typeof shouldContinue === 'function' && !shouldContinue()) {
                resolve(false);
                return;
            }
            const progress = Math.min((currentTime - startTime) / total, 1);
            apply(progress);
            if (progress < 1) requestAnimationFrame(step);
            else resolve(true);
        };
        requestAnimationFrame(step);
    });
}

Live2DManager.prototype.isLive2DGameModeEdgePeekActive = function () {
    const state = this._live2DGameModeEdgePeekState;
    return !!(state && state.active && state.model && !state.model.destroyed);
};

Live2DManager.prototype.clearLive2DGameModeEdgePeek = function (reason = 'manual', options = {}) {
    const state = this._live2DGameModeEdgePeekState;
    const model = state && state.model && !state.model.destroyed ? state.model : null;
    const restore = options.restore !== false;
    this._live2DGameModeEdgePeekTransitionId = (this._live2DGameModeEdgePeekTransitionId || 0) + 1;
    if (state && state.active && model) {
        if (restore) {
            model.x = state.baseX;
            model.y = state.baseY;
        }
        model.rotation = state.baseRotation;
        if (model.scale && Number.isFinite(Number(state.baseScaleX))) {
            model.scale.x = state.baseScaleX;
        }
    }
    this._live2DGameModeEdgePeekState = null;
    if (document.body) {
        document.body.classList.remove('neko-live2d-game-mode-edge-peek');
    }
    try {
        window.dispatchEvent(new CustomEvent('neko:live2d-game-mode-edge-peek-changed', {
            detail: { active: false, reason }
        }));
    } catch (_) {}
};

Live2DManager.prototype.restoreLive2DGameModeEdgePeek = async function (reason = 'manual-restore') {
    const state = this._live2DGameModeEdgePeekState;
    const model = state && state.model && !state.model.destroyed ? state.model : null;
    if (!state || !state.active || !model) return false;
    const transitionId = state.transitionId;
    const stillCurrent = () => {
        const activeState = this._live2DGameModeEdgePeekState;
        return !!(activeState &&
            activeState.active &&
            activeState.model === model &&
            activeState.transitionId === transitionId);
    };
    const animated = await animateLive2DGameModeEdgePeekTransform(model, {
        x: state.baseX,
        y: state.baseY,
        rotation: state.baseRotation,
        scaleX: state.baseScaleX
    }, LIVE2D_GAME_MODE_EDGE_PEEK_ANIMATION_MS, stillCurrent);
    if (!animated || !stillCurrent()) return false;
    this.clearLive2DGameModeEdgePeek(reason);
    return true;
};

Live2DManager.prototype._tryApplyLive2DGameModeEdgePeek = async function (model) {
    if (!isLive2DGameModeEdgePeekEnabled()) {
        this.clearLive2DGameModeEdgePeek('game-mode-disabled');
        return false;
    }
    if (isLive2DGameModeEdgePeekDesktopRuntime()) {
        await refreshLive2DGameModeEdgePeekDisplayContext();
    }
    const bounds = getLive2DGameModeEdgePeekBounds(model);
    const target = getLive2DGameModeEdgePeekPlacement(model, bounds, this);
    if (!bounds || !target) {
        this.clearLive2DGameModeEdgePeek('drag-away-from-edge');
        return false;
    }
    this.clearLive2DGameModeEdgePeek('reapply', { restore: false });
    const transitionId = (this._live2DGameModeEdgePeekTransitionId || 0) + 1;
    this._live2DGameModeEdgePeekTransitionId = transitionId;
    const baseX = Number(model.x) || 0;
    const baseY = Number(model.y) || 0;
    const baseRotation = Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0;
    const baseScaleX = model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1;
    this._live2DGameModeEdgePeekState = {
        active: true,
        edge: target.edge,
        side: target.side,
        model,
        baseX,
        baseY,
        baseRotation,
        baseScaleX,
        transitionId,
        peekX: target.x,
        peekY: target.y,
        peekRotation: target.rotation,
        peekScaleX: target.scaleX,
        headAnchored: target.headAnchored,
        headAnchorSource: target.headAnchorSource,
        waistAnchored: target.waistAnchored,
        visibleBounds: target.visibleBounds
    };
    if (document.body) {
        document.body.classList.add('neko-live2d-game-mode-edge-peek');
    }
    const stillCurrent = () => {
        const activeState = this._live2DGameModeEdgePeekState;
        return !!(activeState &&
            activeState.active &&
            activeState.model === model &&
            activeState.transitionId === transitionId);
    };
    const animated = await animateLive2DGameModeEdgePeekTransform(
        model,
        target,
        LIVE2D_GAME_MODE_EDGE_PEEK_ANIMATION_MS,
        stillCurrent
    );
    if (!animated || !stillCurrent()) return false;
    model.x = target.x;
    model.y = target.y;
    model.rotation = target.rotation;
    if (model.scale) {
        model.scale.x = target.scaleX;
    }
    const finalBounds = getLive2DGameModeEdgePeekBounds(model);
    const viewport = getLive2DGameModeEdgePeekViewport(finalBounds || bounds, this);
    const visibleBounds = getLive2DGameModeEdgePeekViewportIntersection(finalBounds, viewport) || target.visibleBounds;
    if (this._live2DGameModeEdgePeekState && this._live2DGameModeEdgePeekState.model === model) {
        this._live2DGameModeEdgePeekState.visibleBounds = visibleBounds;
    }
    try {
        window.dispatchEvent(new CustomEvent('neko:live2d-game-mode-edge-peek-changed', {
            detail: { active: true, edge: target.edge, visibleBounds }
        }));
    } catch (_) {}
    return true;
};

function clearLive2DGameModeEdgePeek(reason, options) {
    const manager = window.live2dManager;
    if (manager && typeof manager.clearLive2DGameModeEdgePeek === 'function') {
        manager.clearLive2DGameModeEdgePeek(reason, options);
    } else if (document.body) {
        document.body.classList.remove('neko-live2d-game-mode-edge-peek');
    }
}

function clearLive2DGameModeEdgePeekOnDisabled(event) {
    const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
    if (detail.enabled === false) {
        clearLive2DGameModeEdgePeek('game-mode-disabled');
    }
}

function clearLive2DGameModeEdgePeekOnGoodbye() {
    clearLive2DGameModeEdgePeek('live2d-goodbye');
}

function captureLive2DGameModeEdgePeekRestoreAnchor() {
    const manager = window.live2dManager;
    const state = manager && manager._live2DGameModeEdgePeekState;
    const model = state && state.active && state.model && !state.model.destroyed ? state.model : null;
    const edge = state && String(state.edge || state.side || '');
    const validEdges = ['left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];
    if (!manager || !model || !validEdges.includes(edge)) return null;
    const bounds = getLive2DGameModeEdgePeekBounds(model);
    const viewport = getLive2DGameModeEdgePeekViewport(bounds, manager);
    const visibleBounds = state.visibleBounds || getLive2DGameModeEdgePeekViewportIntersection(bounds, viewport);
    if (!viewport || !visibleBounds || viewport.height <= 0) return null;
    return {
        kind: 'live2d-edge-peek',
        edge,
        side: state.side,
        edgeAnchorRatio: clampLive2DGameModeEdgePeekCoordinate(
            visibleBounds.centerY / viewport.height,
            0,
            1
        ),
        facing: 'inward',
        display: {
            id: String((window.screen && (window.screen.id || window.screen.deviceId)) || ''),
            width: viewport.width,
            height: viewport.height,
            scaleFactor: Number(window.devicePixelRatio) || 1
        }
    };
}

async function restoreLive2DGameModeEdgePeekAnchor(anchor) {
    if (!anchor || anchor.kind !== 'live2d-edge-peek') return false;
    const manager = window.live2dManager;
    const model = manager && manager.currentModel && !manager.currentModel.destroyed
        ? manager.currentModel
        : null;
    const edge = String(anchor.edge || anchor.side || '');
    const validEdges = ['left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];
    const side = edge.endsWith('left') || edge === 'left' ? 'left' : (edge.endsWith('right') || edge === 'right' ? 'right' : '');
    if (!manager || !model || !validEdges.includes(edge) || !side) return false;
    manager.clearLive2DGameModeEdgePeek('game-mode-anchor-prepare', { restore: false });
    let bounds = getLive2DGameModeEdgePeekBounds(model);
    const viewport = getLive2DGameModeEdgePeekViewport(bounds, manager);
    if (!bounds || !viewport) return false;
    if (edge.startsWith('top-')) {
        model.y += viewport.top - bounds.top;
    } else if (edge.startsWith('bottom-')) {
        model.y += viewport.bottom - bounds.bottom;
    } else {
        const ratio = clampLive2DGameModeEdgePeekCoordinate(Number(anchor.edgeAnchorRatio), 0, 1);
        const targetCenterY = viewport.top + viewport.height * ratio;
        model.y += targetCenterY - (bounds.top + bounds.height / 2);
    }
    bounds = getLive2DGameModeEdgePeekBounds(model);
    if (!bounds) return false;
    model.x += side === 'left'
        ? viewport.left - bounds.left
        : viewport.right - bounds.right;
    return await manager._tryApplyLive2DGameModeEdgePeek(model);
}

if (typeof window !== 'undefined') {
    window.nekoLive2DGameModeEdgePeek = {
        clear: clearLive2DGameModeEdgePeek,
        isEnabled: isLive2DGameModeEdgePeekEnabled,
        captureRestoreAnchor: captureLive2DGameModeEdgePeekRestoreAnchor,
        restoreAnchor: restoreLive2DGameModeEdgePeekAnchor
    };
    window.addEventListener('neko:game-mode-beta-state', clearLive2DGameModeEdgePeekOnDisabled);
    window.addEventListener('live2d-goodbye-click', clearLive2DGameModeEdgePeekOnGoodbye);
    if (isLive2DGameModeEdgePeekDesktopRuntime()) {
        void refreshLive2DGameModeEdgePeekDisplayContext();
        window.addEventListener('electron-display-changed', () => {
            live2DGameModeEdgePeekDisplayContext = null;
            void refreshLive2DGameModeEdgePeekDisplayContext(true);
        });
    }
}

const LIVE2D_PEEK_TRIGGER_RATIO = 0.025;
const LIVE2D_PEEK_TRIGGER_MIN_PX = 12;
const LIVE2D_PEEK_TRIGGER_MAX_PX = 24;
const LIVE2D_PEEK_VISIBLE_RATIO = 0.22;
const LIVE2D_PEEK_VISIBLE_MIN_PX = 96;
const LIVE2D_PEEK_VISIBLE_MAX_PX = 180;
const LIVE2D_PEEK_SIDE_ROTATION_DEGREES = 60;
const LIVE2D_PEEK_CORNER_ROTATION_DEGREES = 45;
const LIVE2D_PEEK_TOP_CORNER_ROTATION_DEGREES = 135;
const LIVE2D_PEEK_HEAD_Y_RATIO = 0.24;
const LIVE2D_PEEK_VISIBLE_MARGIN_PX = 8;
const LIVE2D_PEEK_ANIMATION_MS = 200;
let live2DPeekDisplayContext = null;
let live2DPeekDisplayRefresh = null;

function isLive2DPeekMacRuntime() {
    try {
        return !!(window.__NEKO_DESKTOP_RUNTIME__ &&
            window.__NEKO_DESKTOP_RUNTIME__.platform === 'darwin');
    } catch (_) {
        return false;
    }
}

function isLive2DPeekDesktopRuntime() {
    try {
        return isLive2DPeekMacRuntime() || !!(
            window.electronScreen &&
            typeof window.electronScreen.getCurrentDisplay === 'function'
        );
    } catch (_) {
        return false;
    }
}

function normalizeLive2DPeekRect(rect) {
    if (!rect) return null;
    const x = Number(rect.x);
    const y = Number(rect.y);
    const width = Number(rect.width);
    const height = Number(rect.height);
    if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return { x, y, width, height };
}

function refreshLive2DPeekDisplayContext(force = false) {
    if (!isLive2DPeekDesktopRuntime()) {
        live2DPeekDisplayContext = null;
        return Promise.resolve(null);
    }
    const electronScreen = window.electronScreen;
    if (!electronScreen || typeof electronScreen.getCurrentDisplay !== 'function') {
        live2DPeekDisplayContext = null;
        return Promise.resolve(null);
    }
    if (!force && live2DPeekDisplayContext) {
        return Promise.resolve(live2DPeekDisplayContext);
    }
    if (live2DPeekDisplayRefresh) {
        return live2DPeekDisplayRefresh;
    }

    live2DPeekDisplayRefresh = Promise.resolve()
        .then(() => electronScreen.getCurrentDisplay())
        .then((displayInfo) => {
            const workArea = normalizeLive2DPeekRect(displayInfo && displayInfo.workArea);
            const displayBounds = normalizeLive2DPeekRect(displayInfo && displayInfo.bounds);
            const screenX = Number.isFinite(Number(displayInfo && displayInfo.screenX))
                ? Number(displayInfo.screenX)
                : (displayBounds ? Number(displayBounds.x) : NaN);
            const screenY = Number.isFinite(Number(displayInfo && displayInfo.screenY))
                ? Number(displayInfo.screenY)
                : (displayBounds ? Number(displayBounds.y) : NaN);
            live2DPeekDisplayContext = workArea &&
                Number.isFinite(screenX) && Number.isFinite(screenY)
                ? { screenX, screenY, workArea }
                : null;
            return live2DPeekDisplayContext;
        })
        .catch(() => {
            live2DPeekDisplayContext = null;
            return null;
        })
        .finally(() => {
            live2DPeekDisplayRefresh = null;
        });
    return live2DPeekDisplayRefresh;
}

function getLive2DPeekTriggerViewport(viewport) {
    const context = isLive2DPeekDesktopRuntime()
        ? live2DPeekDisplayContext
        : null;
    if (!viewport || !context || !context.workArea) return viewport;

    const area = context.workArea;
    const left = clampLive2DPeekCoordinate(
        area.x - context.screenX,
        viewport.left,
        viewport.right
    );
    const top = clampLive2DPeekCoordinate(
        area.y - context.screenY,
        viewport.top,
        viewport.bottom
    );
    const right = clampLive2DPeekCoordinate(
        area.x + area.width - context.screenX,
        viewport.left,
        viewport.right
    );
    const bottom = clampLive2DPeekCoordinate(
        area.y + area.height - context.screenY,
        viewport.top,
        viewport.bottom
    );
    if (right <= left || bottom <= top) return viewport;
    return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top
    };
}

function isLive2DPeekEnabled() {
    try {
        return !!(window.nekoWidgetMode &&
            typeof window.nekoWidgetMode.isEnabled === 'function' &&
            window.nekoWidgetMode.isEnabled());
    } catch (_) {
        return false;
    }
}

function getLive2DPeekBounds(model) {
    if (!model || typeof model.getBounds !== 'function') return null;
    let bounds = null;
    try {
        bounds = model.getBounds();
    } catch (_) {
        return null;
    }
    if (!bounds) return null;
    const left = Number.isFinite(bounds.left) ? bounds.left : bounds.x;
    const top = Number.isFinite(bounds.top) ? bounds.top : bounds.y;
    const right = Number.isFinite(bounds.right) ? bounds.right : left + bounds.width;
    const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : top + bounds.height;
    const width = Number.isFinite(bounds.width) ? bounds.width : right - left;
    const height = Number.isFinite(bounds.height) ? bounds.height : bottom - top;
    if (![left, top, right, bottom, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return { left, top, right, bottom, width, height };
}

function clampLive2DPeekCoordinate(value, min, max) {
    if (!Number.isFinite(value)) return min;
    return Math.min(max, Math.max(min, value));
}

function getLive2DPeekViewport(bounds = null, manager = null) {
    const fallbackW = bounds && Number.isFinite(bounds.width) ? bounds.width : 1;
    const fallbackH = bounds && Number.isFinite(bounds.height) ? bounds.height : 1;
    const renderer = manager && manager.pixi_app && manager.pixi_app.renderer;
    const screen = renderer && renderer.screen;
    const rendererW = Number(screen && screen.width);
    const rendererH = Number(screen && screen.height);
    const viewportW = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : Number(window.innerWidth);
    const viewportH = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : Number(window.innerHeight);
    const width = Number.isFinite(viewportW) && viewportW > 0 ? viewportW : fallbackW;
    const height = Number.isFinite(viewportH) && viewportH > 0 ? viewportH : fallbackH;
    return { left: 0, top: 0, right: width, bottom: height, width, height };
}

function getLive2DPeekViewportIntersection(bounds, viewport) {
    if (!bounds || !viewport) return null;
    const left = Math.max(bounds.left, viewport.left);
    const right = Math.min(bounds.right, viewport.right);
    const top = Math.max(bounds.top, viewport.top);
    const bottom = Math.min(bounds.bottom, viewport.bottom);
    const width = right - left;
    const height = bottom - top;
    if (![left, right, top, bottom, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
        return null;
    }
    return {
        left,
        right,
        top,
        bottom,
        width,
        height,
        centerX: left + width / 2,
        centerY: top + height / 2
    };
}

function getLive2DPeekAnchor(bounds, viewport) {
    if (!bounds || !viewport) return null;
    const horizontalThreshold = clampLive2DPeekCoordinate(
        bounds.width * LIVE2D_PEEK_TRIGGER_RATIO,
        LIVE2D_PEEK_TRIGGER_MIN_PX,
        LIVE2D_PEEK_TRIGGER_MAX_PX
    );
    const verticalThreshold = clampLive2DPeekCoordinate(
        bounds.height * LIVE2D_PEEK_TRIGGER_RATIO,
        LIVE2D_PEEK_TRIGGER_MIN_PX,
        LIVE2D_PEEK_TRIGGER_MAX_PX
    );
    const nearLeft = bounds.left <= viewport.left + horizontalThreshold;
    const nearRight = viewport.right - bounds.right <= horizontalThreshold;
    if (!nearLeft && !nearRight) return null;
    const nearTop = bounds.top <= viewport.top + verticalThreshold;
    const nearBottom = viewport.bottom - bounds.bottom <= verticalThreshold;
    const side = nearLeft && nearRight
        ? (bounds.left <= viewport.right - bounds.right ? 'left' : 'right')
        : (nearLeft ? 'left' : 'right');
    let verticalEdge = '';
    if (nearTop && nearBottom) {
        verticalEdge = bounds.top + bounds.height / 2 <= viewport.top + viewport.height / 2
            ? 'top'
            : 'bottom';
    } else if (nearTop) {
        verticalEdge = 'top';
    } else if (nearBottom) {
        verticalEdge = 'bottom';
    }
    return {
        edge: verticalEdge ? `${verticalEdge}-${side}` : side,
        side,
        verticalEdge
    };
}

function getLive2DPeekRotationDegrees(anchor) {
    if (!anchor || !anchor.side) return 0;
    if (!anchor.verticalEdge) {
        return anchor.side === 'left'
            ? LIVE2D_PEEK_SIDE_ROTATION_DEGREES
            : -LIVE2D_PEEK_SIDE_ROTATION_DEGREES;
    }
    if (anchor.verticalEdge === 'top') {
        return anchor.side === 'left'
            ? LIVE2D_PEEK_TOP_CORNER_ROTATION_DEGREES
            : -LIVE2D_PEEK_TOP_CORNER_ROTATION_DEGREES;
    }
    return anchor.side === 'left'
        ? LIVE2D_PEEK_CORNER_ROTATION_DEGREES
        : -LIVE2D_PEEK_CORNER_ROTATION_DEGREES;
}

function getLive2DPeekRevealWidth(bounds) {
    if (!bounds) return LIVE2D_PEEK_VISIBLE_MIN_PX;
    const width = clampLive2DPeekCoordinate(
        bounds.width * LIVE2D_PEEK_VISIBLE_RATIO,
        LIVE2D_PEEK_VISIBLE_MIN_PX,
        LIVE2D_PEEK_VISIBLE_MAX_PX
    );
    return Math.min(bounds.width, width);
}

function getLive2DPeekHeadAnchor(manager) {
    if (!manager || typeof manager.getHeadScreenAnchor !== 'function') return null;
    try {
        const anchor = manager.getHeadScreenAnchor();
        const x = Number(anchor && anchor.x);
        const y = Number(anchor && anchor.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DPeekBodyRect(manager) {
    if (!manager || typeof manager.getBodyScreenRectInfo !== 'function') return null;
    try {
        const info = manager.getBodyScreenRectInfo();
        const rect = info && info.rect;
        const centerX = Number(rect && (Number.isFinite(rect.centerX)
            ? rect.centerX
            : Number(rect.left) + Number(rect.width) * 0.5));
        const bottom = Number(rect && rect.bottom);
        return Number.isFinite(centerX) && Number.isFinite(bottom) ? { centerX, bottom } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DPeekFallbackHeadLocalPoint(model, bounds) {
    if (!model || !bounds || typeof model.toLocal !== 'function' || typeof model.toGlobal !== 'function') {
        return null;
    }
    try {
        const localPoint = model.toLocal({
            x: bounds.left + bounds.width * 0.5,
            y: bounds.top + bounds.height * LIVE2D_PEEK_HEAD_Y_RATIO
        });
        const x = Number(localPoint && localPoint.x);
        const y = Number(localPoint && localPoint.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DPeekGlobalPoint(model, localPoint) {
    if (!model || !localPoint || typeof model.toGlobal !== 'function') return null;
    try {
        const point = model.toGlobal(localPoint);
        const x = Number(point && point.x);
        const y = Number(point && point.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
    } catch (_) {
        return null;
    }
}

function getLive2DPeekInwardScaleX(model, side) {
    const rawScaleX = model && model.scale && Number.isFinite(Number(model.scale.x))
        ? Number(model.scale.x)
        : 1;
    const baseScaleX = rawScaleX === 0 ? 1 : rawScaleX;
    return side === 'left' ? Math.abs(baseScaleX) : -Math.abs(baseScaleX);
}

function getLive2DPeekVerticalCorrection(bounds, viewport) {
    if (!bounds || !viewport) return 0;
    const margin = LIVE2D_PEEK_VISIBLE_MARGIN_PX;
    if (bounds.bottom < viewport.top + margin) return viewport.top + margin - bounds.bottom;
    if (bounds.top > viewport.bottom - margin) return viewport.bottom - margin - bounds.top;
    return 0;
}

function getLive2DPeekPlacement(model, bounds, manager = null) {
    if (!model || !bounds) return null;
    const viewport = getLive2DPeekViewport(bounds, manager);
    const triggerViewport = getLive2DPeekTriggerViewport(viewport);
    const anchor = getLive2DPeekAnchor(bounds, triggerViewport);
    if (!anchor) return null;
    const { edge, side, verticalEdge } = anchor;

    const baseX = Number(model.x) || 0;
    const baseY = Number(model.y) || 0;
    const baseRotation = Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0;
    const baseScaleX = model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1;
    const targetRotationDegrees = getLive2DPeekRotationDegrees(anchor);
    const targetRotation = targetRotationDegrees * Math.PI / 180;
    const targetScaleX = getLive2DPeekInwardScaleX(model, side);
    const baseHeadAnchor = getLive2DPeekHeadAnchor(manager);
    const fallbackHeadLocalPoint = baseHeadAnchor
        ? null
        : getLive2DPeekFallbackHeadLocalPoint(model, bounds);
    const fallbackBaseHeadAnchor = getLive2DPeekGlobalPoint(model, fallbackHeadLocalPoint);
    const effectiveBaseHeadAnchor = baseHeadAnchor || fallbackBaseHeadAnchor;
    const baseBodyRect = getLive2DPeekBodyRect(manager);
    const baseHeadY = effectiveBaseHeadAnchor
        ? effectiveBaseHeadAnchor.y
        : bounds.top + bounds.height * LIVE2D_PEEK_HEAD_Y_RATIO;
    const desiredHeadY = clampLive2DPeekCoordinate(
        baseHeadY,
        viewport.top + LIVE2D_PEEK_VISIBLE_MARGIN_PX,
        viewport.bottom - LIVE2D_PEEK_VISIBLE_MARGIN_PX
    );

    let transformedBounds = null;
    let transformedHeadAnchor = null;
    let transformedHeadAnchorSource = '';
    let transformedBodyRect = null;
    try {
        model.x = baseX;
        model.y = baseY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        transformedBounds = getLive2DPeekBounds(model);
        transformedHeadAnchor = getLive2DPeekHeadAnchor(manager);
        if (transformedHeadAnchor) {
            transformedHeadAnchorSource = 'manager';
        } else {
            transformedHeadAnchor = getLive2DPeekGlobalPoint(model, fallbackHeadLocalPoint);
            if (transformedHeadAnchor) transformedHeadAnchorSource = 'bounds-fallback';
        }
        transformedBodyRect = getLive2DPeekBodyRect(manager);
    } catch (_) {
        transformedBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    if (!transformedBounds) return null;

    const revealWidth = getLive2DPeekRevealWidth(transformedBounds);
    const headInset = clampLive2DPeekCoordinate(revealWidth * 0.42, 48, 84);
    const desiredHeadX = side === 'left'
        ? viewport.left + headInset
        : viewport.right - headInset;
    const useHeadAnchor = !!verticalEdge && !!transformedHeadAnchor;
    const useWaistAnchor = !verticalEdge && !!(baseBodyRect && transformedBodyRect);
    const desiredWaistX = side === 'left' ? viewport.left - 8 : viewport.right + 8;
    let offsetX = useHeadAnchor
        ? desiredHeadX - transformedHeadAnchor.x
        : (useWaistAnchor
        ? desiredWaistX - transformedBodyRect.centerX
        : (transformedHeadAnchor
            ? desiredHeadX - transformedHeadAnchor.x
            : (side === 'left'
            ? viewport.left + revealWidth - transformedBounds.right
            : viewport.right - revealWidth - transformedBounds.left)));
    const targetHeadY = transformedHeadAnchor
        ? transformedHeadAnchor.y
        : transformedBounds.top + transformedBounds.height * LIVE2D_PEEK_HEAD_Y_RATIO;
    let offsetY;
    if (useHeadAnchor) {
        const desiredHeadInsetY = clampLive2DPeekCoordinate(revealWidth * 0.32, 36, 64);
        const desiredHeadYAtEdge = verticalEdge === 'bottom'
            ? viewport.bottom - desiredHeadInsetY
            : viewport.top + desiredHeadInsetY;
        offsetY = desiredHeadYAtEdge - transformedHeadAnchor.y;
    } else if (useWaistAnchor) {
        offsetY = baseBodyRect.bottom - transformedBodyRect.bottom;
    } else if (verticalEdge === 'top') {
        offsetY = viewport.top + revealWidth - transformedBounds.bottom;
    } else if (verticalEdge === 'bottom') {
        offsetY = viewport.bottom - revealWidth - transformedBounds.top;
    } else {
        offsetY = desiredHeadY - targetHeadY;
    }

    let targetBounds = null;
    try {
        model.x = baseX + offsetX;
        model.y = baseY + offsetY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        targetBounds = getLive2DPeekBounds(model);
    } catch (_) {
        targetBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    if (!targetBounds) return null;
    offsetY += getLive2DPeekVerticalCorrection(targetBounds, viewport);

    try {
        model.x = baseX + offsetX;
        model.y = baseY + offsetY;
        model.rotation = targetRotation;
        if (model.scale) model.scale.x = targetScaleX;
        targetBounds = getLive2DPeekBounds(model);
    } catch (_) {
        targetBounds = null;
    } finally {
        model.x = baseX;
        model.y = baseY;
        model.rotation = baseRotation;
        if (model.scale) model.scale.x = baseScaleX;
    }
    const visibleBounds = getLive2DPeekViewportIntersection(targetBounds, viewport);
    if (!visibleBounds) return null;

    return {
        edge,
        side,
        x: baseX + offsetX,
        y: baseY + offsetY,
        rotation: targetRotation,
        rotationDegrees: targetRotationDegrees,
        scaleX: targetScaleX,
        headAnchored: useHeadAnchor || !!transformedHeadAnchor,
        headAnchorSource: transformedHeadAnchorSource,
        waistAnchored: useWaistAnchor,
        revealWidth,
        visibleBounds
    };
}

function animateLive2DPeekTransform(model, target, duration = LIVE2D_PEEK_ANIMATION_MS, shouldContinue = null) {
    return new Promise((resolve) => {
        if (!model || model.destroyed || !target) {
            resolve(false);
            return;
        }
        const start = {
            x: Number(model.x) || 0,
            y: Number(model.y) || 0,
            rotation: Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0,
            scaleX: model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1
        };
        const startTime = performance.now();
        const total = Math.max(0, Number(duration) || 0);
        const apply = (progress) => {
            const eased = EasingFunctions.easeOutCubic(progress);
            model.x = start.x + (target.x - start.x) * eased;
            model.y = start.y + (target.y - start.y) * eased;
            model.rotation = start.rotation + (target.rotation - start.rotation) * eased;
            if (model.scale) {
                model.scale.x = start.scaleX + (target.scaleX - start.scaleX) * eased;
            }
        };
        if (total <= 0) {
            if (typeof shouldContinue === 'function' && !shouldContinue()) {
                resolve(false);
                return;
            }
            apply(1);
            resolve(true);
            return;
        }
        const step = (currentTime) => {
            if (!model || model.destroyed) {
                resolve(false);
                return;
            }
            if (typeof shouldContinue === 'function' && !shouldContinue()) {
                resolve(false);
                return;
            }
            const progress = Math.min((currentTime - startTime) / total, 1);
            apply(progress);
            if (progress < 1) requestAnimationFrame(step);
            else resolve(true);
        };
        requestAnimationFrame(step);
    });
}

Live2DManager.prototype.isLive2DPeekActive = function () {
    const state = this._live2DPeekState;
    return !!(state && state.active && state.model && !state.model.destroyed);
};

Live2DManager.prototype.clearLive2DPeek = function (reason = 'manual', options = {}) {
    const state = this._live2DPeekState;
    const model = state && state.model && !state.model.destroyed ? state.model : null;
    const restore = options.restore !== false;
    this._live2DPeekTransitionId = (this._live2DPeekTransitionId || 0) + 1;
    if (state && state.active && model) {
        if (restore) {
            model.x = state.baseX;
            model.y = state.baseY;
        }
        model.rotation = state.baseRotation;
        if (model.scale && Number.isFinite(Number(state.baseScaleX))) {
            model.scale.x = state.baseScaleX;
        }
    }
    this._live2DPeekState = null;
    if (document.body) {
        document.body.classList.remove('neko-live2d-peek');
    }
    try {
        window.dispatchEvent(new CustomEvent('neko:live2d-peek-changed', {
            detail: { active: false, reason }
        }));
    } catch (_) {}
};

Live2DManager.prototype.restoreLive2DPeek = async function (reason = 'manual-restore') {
    const state = this._live2DPeekState;
    const model = state && state.model && !state.model.destroyed ? state.model : null;
    if (!state || !state.active || !model) return false;
    const transitionId = state.transitionId;
    const stillCurrent = () => {
        const activeState = this._live2DPeekState;
        return !!(activeState &&
            activeState.active &&
            activeState.model === model &&
            activeState.transitionId === transitionId);
    };
    const animated = await animateLive2DPeekTransform(model, {
        x: state.baseX,
        y: state.baseY,
        rotation: state.baseRotation,
        scaleX: state.baseScaleX
    }, LIVE2D_PEEK_ANIMATION_MS, stillCurrent);
    if (!animated || !stillCurrent()) return false;
    this.clearLive2DPeek(reason);
    return true;
};

Live2DManager.prototype._tryApplyLive2DPeek = async function (model) {
    if (!isLive2DPeekEnabled()) {
        this.clearLive2DPeek('widget-mode-disabled');
        return false;
    }
    if (isLive2DPeekDesktopRuntime()) {
        await refreshLive2DPeekDisplayContext();
    }
    const bounds = getLive2DPeekBounds(model);
    const target = getLive2DPeekPlacement(model, bounds, this);
    if (!bounds || !target) {
        this.clearLive2DPeek('drag-away-from-edge');
        return false;
    }
    this.clearLive2DPeek('reapply', { restore: false });
    const transitionId = (this._live2DPeekTransitionId || 0) + 1;
    this._live2DPeekTransitionId = transitionId;
    const baseX = Number(model.x) || 0;
    const baseY = Number(model.y) || 0;
    const baseRotation = Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0;
    const baseScaleX = model.scale && Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1;
    this._live2DPeekState = {
        active: true,
        edge: target.edge,
        side: target.side,
        model,
        baseX,
        baseY,
        baseRotation,
        baseScaleX,
        transitionId,
        peekX: target.x,
        peekY: target.y,
        peekRotation: target.rotation,
        peekScaleX: target.scaleX,
        headAnchored: target.headAnchored,
        headAnchorSource: target.headAnchorSource,
        waistAnchored: target.waistAnchored,
        visibleBounds: target.visibleBounds
    };
    if (document.body) {
        document.body.classList.add('neko-live2d-peek');
    }
    const stillCurrent = () => {
        const activeState = this._live2DPeekState;
        return !!(activeState &&
            activeState.active &&
            activeState.model === model &&
            activeState.transitionId === transitionId);
    };
    const animated = await animateLive2DPeekTransform(
        model,
        target,
        LIVE2D_PEEK_ANIMATION_MS,
        stillCurrent
    );
    if (!animated || !stillCurrent()) return false;
    model.x = target.x;
    model.y = target.y;
    model.rotation = target.rotation;
    if (model.scale) {
        model.scale.x = target.scaleX;
    }
    const finalBounds = getLive2DPeekBounds(model);
    const viewport = getLive2DPeekViewport(finalBounds || bounds, this);
    const visibleBounds = getLive2DPeekViewportIntersection(finalBounds, viewport) || target.visibleBounds;
    if (this._live2DPeekState && this._live2DPeekState.model === model) {
        this._live2DPeekState.visibleBounds = visibleBounds;
    }
    try {
        window.dispatchEvent(new CustomEvent('neko:live2d-peek-changed', {
            detail: { active: true, edge: target.edge, visibleBounds }
        }));
    } catch (_) {}
    return true;
};

function clearLive2DPeek(reason, options) {
    const manager = window.live2dManager;
    if (manager && typeof manager.clearLive2DPeek === 'function') {
        manager.clearLive2DPeek(reason, options);
    } else if (document.body) {
        document.body.classList.remove('neko-live2d-peek');
    }
}

function clearLive2DPeekOnDisabled(event) {
    const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
    if (detail.enabled === false) {
        clearLive2DPeek('widget-mode-disabled');
    }
}

function clearLive2DPeekOnGoodbye(event) {
    const restoreAnchor = captureLive2DPeekRestoreAnchor();
    if (restoreAnchor && event) {
        if (event.detail && typeof event.detail === 'object') {
            event.detail.edgeAnchor = restoreAnchor;
        } else {
            event.__nekoLive2DPeekEdgeAnchor = restoreAnchor;
        }
    }
    clearLive2DPeek('live2d-goodbye');
}

function captureLive2DPeekRestoreAnchor() {
    const manager = window.live2dManager;
    const state = manager && manager._live2DPeekState;
    const model = state && state.active && state.model && !state.model.destroyed ? state.model : null;
    const edge = state && String(state.edge || state.side || '');
    const validEdges = ['left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];
    if (!manager || !model || !validEdges.includes(edge)) return null;
    const bounds = getLive2DPeekBounds(model);
    const viewport = getLive2DPeekViewport(bounds, manager);
    const visibleBounds = state.visibleBounds || getLive2DPeekViewportIntersection(bounds, viewport);
    if (!viewport || !visibleBounds || viewport.height <= 0) return null;
    return {
        kind: 'live2d-edge-peek',
        edge,
        side: state.side,
        edgeAnchorRatio: clampLive2DPeekCoordinate(
            visibleBounds.centerY / viewport.height,
            0,
            1
        ),
        facing: 'inward',
        display: {
            id: String((window.screen && (window.screen.id || window.screen.deviceId)) || ''),
            width: viewport.width,
            height: viewport.height,
            scaleFactor: Number(window.devicePixelRatio) || 1
        }
    };
}

async function restoreLive2DPeekAnchor(anchor) {
    if (!anchor || anchor.kind !== 'live2d-edge-peek') return false;
    const manager = window.live2dManager;
    const model = manager && manager.currentModel && !manager.currentModel.destroyed
        ? manager.currentModel
        : null;
    const edge = String(anchor.edge || anchor.side || '');
    const validEdges = ['left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];
    const side = edge.endsWith('left') || edge === 'left' ? 'left' : (edge.endsWith('right') || edge === 'right' ? 'right' : '');
    if (!manager || !model || !validEdges.includes(edge) || !side) return false;
    manager.clearLive2DPeek('widget-mode-anchor-prepare', { restore: false });
    let bounds = getLive2DPeekBounds(model);
    const viewport = getLive2DPeekViewport(bounds, manager);
    if (!bounds || !viewport) return false;
    if (edge.startsWith('top-')) {
        model.y += viewport.top - bounds.top;
    } else if (edge.startsWith('bottom-')) {
        model.y += viewport.bottom - bounds.bottom;
    } else {
        const ratio = clampLive2DPeekCoordinate(Number(anchor.edgeAnchorRatio), 0, 1);
        const targetCenterY = viewport.top + viewport.height * ratio;
        model.y += targetCenterY - (bounds.top + bounds.height / 2);
    }
    bounds = getLive2DPeekBounds(model);
    if (!bounds) return false;
    model.x += side === 'left'
        ? viewport.left - bounds.left
        : viewport.right - bounds.right;
    return await manager._tryApplyLive2DPeek(model);
}

if (typeof window !== 'undefined') {
    window.nekoLive2DPeek = {
        clear: clearLive2DPeek,
        isEnabled: isLive2DPeekEnabled,
        captureRestoreAnchor: captureLive2DPeekRestoreAnchor,
        restoreAnchor: restoreLive2DPeekAnchor
    };
    window.addEventListener('neko:widget-mode-state-changed', clearLive2DPeekOnDisabled);
    window.addEventListener('live2d-goodbye-click', clearLive2DPeekOnGoodbye);
    if (isLive2DPeekDesktopRuntime()) {
        void refreshLive2DPeekDisplayContext();
        window.addEventListener('electron-display-changed', () => {
            live2DPeekDisplayContext = null;
            void refreshLive2DPeekDisplayContext(true);
        });
    }
}

/**
 * 检测模型是否超出当前屏幕边界，并计算吸附目标位置
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} options - 可选参数
 * @param {boolean} options.afterDisplaySwitch - 是否为屏幕切换后的吸附（使用更宽松的条件：超出即吸附）
 * @param {number} options.threshold - 可选吸附阈值；初始摆放等旧调用可传入更宽松阈值
 * @returns {Object|null} 返回吸附信息，如果不需要吸附则返回 null
 */
Live2DManager.prototype._checkSnapRequired = async function (model, options = {}) {
    if (!model) return null;

    const { afterDisplaySwitch = false, threshold: customThreshold } = options;

    try {
        const bounds = model.getBounds();
        const modelLeft = bounds.left;
        const modelRight = bounds.right;
        const modelTop = bounds.top;
        const modelBottom = bounds.bottom;
        const modelWidth = bounds.width;
        const modelHeight = bounds.height;

        // 获取当前屏幕边界
        // 吸附 clamp 范围必须等同于真实可渲染像素（即 Pet 窗口的 CSS 像素尺寸）。
        // 多屏下 currentDisplay.workArea 可能大于当前窗口 innerHeight（窗口还未 resize 到新屏，或屏幕比主屏高），
        // 若直接拿 workArea 作边界，模型会被吸附到窗口像素外、被窗口边界裁成一条水平切割线。
        const renderer = this.pixi_app && this.pixi_app.renderer;
        const rendererScreen = renderer && renderer.screen;
        const rendererW = Number(rendererScreen && rendererScreen.width);
        const rendererH = Number(rendererScreen && rendererScreen.height);
        let screenLeft = 0;
        let screenTop = 0;
        let screenRight = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : window.innerWidth;
        let screenBottom = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : window.innerHeight;

        // 可选：读 workArea 做二次保险（取更小值），但绝不能超过 innerWidth/innerHeight
        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay && currentDisplay.workArea) {
                    const waW = currentDisplay.workArea.width;
                    const waH = currentDisplay.workArea.height;
                    if (Number.isFinite(waW) && waW > 0) screenRight = Math.min(screenRight, waW);
                    if (Number.isFinite(waH) && waH > 0) screenBottom = Math.min(screenBottom, waH);
                }
            } catch (e) {
                console.debug('获取屏幕工作区域失败，使用窗口尺寸');
            }
        }

        // 计算超出边界的距离
        let overflowLeft = screenLeft - modelLeft;       // 左边超出（正值表示超出）
        let overflowRight = modelRight - screenRight;    // 右边超出
        let overflowTop = screenTop - modelTop;          // 上边超出
        let overflowBottom = modelBottom - screenBottom; // 下边超出

        const threshold = customThreshold ?? SNAP_CONFIG.threshold;
        const margin = SNAP_CONFIG.margin;

        // 计算模型在屏幕内剩余的像素数
        const visibleLeft = Math.max(modelLeft, screenLeft);
        const visibleRight = Math.min(modelRight, screenRight);
        const visibleWidth = Math.max(0, visibleRight - visibleLeft);
        const visibleTop = Math.max(modelTop, screenTop);
        const visibleBottom = Math.min(modelBottom, screenBottom);
        const visibleHeight = Math.max(0, visibleBottom - visibleTop);

        // 桌宠窗口与网页端统一按可见面积阈值吸附：只有模型绝大部分出屏才回弹，
        // 贴边摆放不会被过度纠正。多屏切换后仍强制按安全边距吸回当前窗口。
        let needsSnapLeft, needsSnapRight, needsSnapTop, needsSnapBottom;
        if (afterDisplaySwitch) {
            needsSnapLeft = overflowLeft > margin;
            needsSnapRight = overflowRight > margin;
            needsSnapTop = overflowTop > margin;
            needsSnapBottom = overflowBottom > margin;
        } else {
            const needsSnapHorizontal = visibleWidth < threshold && (overflowLeft > 0 || overflowRight > 0);
            const needsSnapVertical = visibleHeight < threshold && (overflowTop > 0 || overflowBottom > 0);
            needsSnapLeft = overflowLeft > 0 && needsSnapHorizontal;
            needsSnapRight = overflowRight > 0 && needsSnapHorizontal;
            needsSnapTop = overflowTop > 0 && needsSnapVertical;
            needsSnapBottom = overflowBottom > 0 && needsSnapVertical;
        }

        if (!needsSnapLeft && !needsSnapRight && !needsSnapTop && !needsSnapBottom) {
            return null; // 不需要吸附
        }

        // 计算目标位置
        let targetX = model.x;
        let targetY = model.y;

        // 水平方向吸附
        if (needsSnapLeft && needsSnapRight) {
            // 模型比屏幕还宽，居中显示
            targetX = model.x + (screenRight - screenLeft) / 2 - (modelLeft + modelWidth / 2);
        } else if (needsSnapLeft) {
            // 左边超出，向右移动
            targetX = model.x + overflowLeft + margin;
        } else if (needsSnapRight) {
            // 右边超出，向左移动
            targetX = model.x - overflowRight - margin;
        }

        // 垂直方向吸附
        if (needsSnapTop && needsSnapBottom) {
            // 模型比屏幕还高，居中显示
            targetY = model.y + (screenBottom - screenTop) / 2 - (modelTop + modelHeight / 2);
        } else if (needsSnapTop) {
            // 上边超出，向下移动
            targetY = model.y + overflowTop + margin;
        } else if (needsSnapBottom) {
            // 下边超出，向上移动
            targetY = model.y - overflowBottom - margin;
        }

        // 验证目标位置
        if (!Number.isFinite(targetX) || !Number.isFinite(targetY)) {
            console.warn('计算的吸附目标位置无效');
            return null;
        }

        // 如果位置变化太小，不执行吸附
        const dx = Math.abs(targetX - model.x);
        const dy = Math.abs(targetY - model.y);
        if (dx < 1 && dy < 1) {
            return null;
        }

        return {
            startX: model.x,
            startY: model.y,
            targetX: targetX,
            targetY: targetY,
            overflow: {
                left: overflowLeft,
                right: overflowRight,
                top: overflowTop,
                bottom: overflowBottom
            }
        };
    } catch (error) {
        console.error('检测吸附时出错:', error);
        return null;
    }
};

/**
 * 执行平滑吸附动画
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} snapInfo - 吸附信息（由 _checkSnapRequired 返回）
 * @returns {Promise<boolean>} 动画完成后返回 true
 */
Live2DManager.prototype._performSnapAnimation = function (model, snapInfo) {
    return new Promise((resolve) => {
        if (!model || !snapInfo) {
            resolve(false);
            return;
        }

        const { startX, startY, targetX, targetY } = snapInfo;
        const duration = SNAP_CONFIG.animationDuration;
        const easingFn = EasingFunctions[SNAP_CONFIG.easingType] || EasingFunctions.easeOutCubic;

        const startTime = performance.now();

        // 标记正在执行吸附动画，防止其他操作干扰
        this._isSnapping = true;

        const animate = (currentTime) => {
            // 检查模型是否仍然有效
            if (!model || model.destroyed) {
                this._isSnapping = false;
                resolve(false);
                return;
            }

            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const easedProgress = easingFn(progress);

            // 计算当前位置
            model.x = startX + (targetX - startX) * easedProgress;
            model.y = startY + (targetY - startY) * easedProgress;

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                // 确保最终位置精确
                model.x = targetX;
                model.y = targetY;
                this._isSnapping = false;

                console.debug('[Live2D] 吸附动画完成，最终位置:', targetX, targetY);
                resolve(true);
            }
        };

        console.debug('[Live2D] 开始吸附动画:', { from: { x: startX, y: startY }, to: { x: targetX, y: targetY } });
        requestAnimationFrame(animate);
    });
};

/**
 * 检测并执行自动吸附（主入口函数）
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} options - 可选参数
 * @param {boolean} options.afterDisplaySwitch - 是否为屏幕切换后的吸附（使用更宽松的条件）
 * @returns {Promise<boolean>} 是否执行了吸附
 */
Live2DManager.prototype._checkAndPerformSnap = async function (model, options = {}) {
    if (!this._isModelReadyForInteraction && !options.allowWhenNotReady) {
        return false;
    }
    // 如果正在执行吸附动画，跳过
    if (this._isSnapping) {
        return false;
    }
    // 跨屏切换期间跳过吸附：窗口 setBounds 与 innerWidth/innerHeight 更新之间有一帧延迟，
    // 中间读到的 clamp 边界会是旧值，触发误吸附。afterDisplaySwitch 路径自己会清标志后再 snap。
    if (this._pendingDisplaySwitch && !options.afterDisplaySwitch) {
        return false;
    }

    const snapInfo = await this._checkSnapRequired(model, options);

    if (!snapInfo) {
        return false;
    }

    console.log('[Live2D] 检测到模型超出屏幕边界，执行自动吸附');
    console.debug('[Live2D] 超出信息:', snapInfo.overflow);

    const animated = await this._performSnapAnimation(model, snapInfo);

    if (animated) {
        // 吸附完成后保存位置
        await this._savePositionAfterInteraction();
    }

    return animated;
};

// 设置拖拽功能
Live2DManager.prototype.setupDragAndDrop = function (model) {
    this.clearLive2DPeek('model-reload');
    this.clearLive2DGameModeEdgePeek('model-reload');
    model.interactive = true;
    // 移除 stage.hitArea = screen，避免阻挡背景点击
    // this.pixi_app.stage.interactive = true;
    // this.pixi_app.stage.hitArea = this.pixi_app.screen;

    this._isDraggingModel = false;
    let dragStartPos = new PIXI.Point();

    // 点击检测相关变量
    let clickStartTime = 0;
    let clickStartX = 0;
    let clickStartY = 0;
    let dragHintStartPointer = null;
    let dragHintLastPointer = null;
    let dragHintApproachShown = false;
    let hasMoved = false;
    let edgePeekStartedDrag = false;
    let edgePeekStartedDragMode = '';
    let edgePeekDragCleared = false;
    const CLICK_THRESHOLD_DISTANCE = 10; // 移动距离阈值（像素）
    const CLICK_THRESHOLD_TIME = 300; // 时间阈值（毫秒）

    const captureDragHintPointer = (event) => {
        const screenX = Number(event?.screenX);
        const screenY = Number(event?.screenY);
        if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return null;
        return { screenX, screenY };
    };

    const recordDragHintPointerEdgeRelease = async () => {
        const helper = window.NekoAvatarMultiScreenDragHint;
        if (!helper || typeof helper.recordPointerEdgeRelease !== 'function') return false;
        if (!dragHintStartPointer || !dragHintLastPointer) return false;
        return await helper.recordPointerEdgeRelease('live2d', {
            startedAt: dragHintStartPointer.startedAt,
            startScreenX: dragHintStartPointer.screenX,
            startScreenY: dragHintStartPointer.screenY,
            screenX: dragHintLastPointer.screenX,
            screenY: dragHintLastPointer.screenY
        });
    };

    const recordDragHintPointerEdgeApproach = async () => {
        const helper = window.NekoAvatarMultiScreenDragHint;
        if (!helper || typeof helper.recordPointerEdgeApproach !== 'function') return false;
        if (dragHintApproachShown || !dragHintStartPointer || !dragHintLastPointer) return false;
        const shown = await helper.recordPointerEdgeApproach('live2d', {
            startedAt: dragHintStartPointer.startedAt,
            startScreenX: dragHintStartPointer.screenX,
            startScreenY: dragHintStartPointer.screenY,
            screenX: dragHintLastPointer.screenX,
            screenY: dragHintLastPointer.screenY
        });
        if (shown) dragHintApproachShown = true;
        return shown;
    };

    // 使用 avatar-ui-drag.js 中的共享工具函数（按钮 pointer-events 管理）
    const disableButtonPointerEvents = () => {
        if (window.DragHelpers) {
            window.DragHelpers.disableButtonPointerEvents();
        }
    };

    const restoreButtonPointerEvents = () => {
        if (window.DragHelpers) {
            window.DragHelpers.restoreButtonPointerEvents();
        }
    };

    const isYuiGuideDragLocked = () => {
        const body = document.body;
        return !!(body && (
            body.classList.contains('yui-guide-home-ui-suppressed')
            || body.classList.contains('yui-taking-over')
        ));
    };

    // 点击触发随机表情和动作（低优先级，会自动恢复）
    // 使用最低优先级 IDLE=1，确保不会覆盖对话等高优先级动作
    window.live2dManager.CLICK_MOTION_PRIORITY = 2; // IDLE priority
    window.live2dManager.CLICK_EFFECT_DURATION = 5000; // 点击效果持续时间（毫秒）

   

    model.on('pointerdown', (event) => {
        if (!this._isModelReadyForInteraction) return;
        if (this.isLocked) return;
        if (isYuiGuideDragLocked()) return;

        // 检测是否为触摸事件，且是多点触摸（双指缩放）
        const originalEvent = event.data.originalEvent;
        if (originalEvent && originalEvent.touches && originalEvent.touches.length > 1) {
            // 多点触摸时不启动拖拽
            return;
        }

        const edgePeekOnPointerDown = this.isLive2DPeekActive();
        const gameModePeekOnPointerDown = this.isLive2DGameModeEdgePeekActive();
        edgePeekStartedDrag = edgePeekOnPointerDown || gameModePeekOnPointerDown;
        edgePeekStartedDragMode = edgePeekOnPointerDown
            ? 'widget'
            : (gameModePeekOnPointerDown ? 'game-mode' : '');
        edgePeekDragCleared = false;
        if (!edgePeekOnPointerDown) {
            this.clearLive2DPeek('drag-start');
        }
        if (!gameModePeekOnPointerDown) {
            this.clearLive2DGameModeEdgePeek('drag-start');
        }
        this._isDraggingModel = true;
        if (typeof this.boostLinuxX11InteractiveFPS === 'function') {
            this.boostLinuxX11InteractiveFPS(1400);
        }
        this.isFocusing = false; // 拖拽时禁用聚焦
        const globalPos = event.data.global;
        dragStartPos.x = globalPos.x - model.x;
        dragStartPos.y = globalPos.y - model.y;

        // 记录点击开始信息
        clickStartTime = Date.now();
        clickStartX = globalPos.x;
        clickStartY = globalPos.y;
        dragHintStartPointer = captureDragHintPointer(originalEvent);
        if (dragHintStartPointer) {
            dragHintStartPointer.startedAt = Date.now();
        }
        dragHintLastPointer = dragHintStartPointer;
        dragHintApproachShown = false;
        hasMoved = false;
        this._touchSetPointerSeq = (this._touchSetPointerSeq || 0) + 1;
        this._lastTouchPointer = { x: clickStartX, y: clickStartY, time: clickStartTime, seq: this._touchSetPointerSeq };
        this._lastTouchHitAreas = [];
        this._lastTouchHitSeq = this._touchSetPointerSeq;
        this._lastPointerDownCustomTouchAreaId = typeof this._getCustomTouchAreaIdAtPoint === 'function'
            ? this._getCustomTouchAreaIdAtPoint(clickStartX, clickStartY)
            : null;

        document.getElementById('live2d-canvas').style.cursor = 'grabbing';

        // 开始拖动时，临时禁用按钮的 pointer-events
        disableButtonPointerEvents();
    });

    const onDragEnd = async (event) => {
        if (this._isDraggingModel) {
            this._isDraggingModel = false;
            document.getElementById('live2d-canvas').style.cursor = '';
            restoreButtonPointerEvents();
            dragHintLastPointer = captureDragHintPointer(event) || dragHintLastPointer;

            if (!this._isModelReadyForInteraction) return;

            // 检测是否为点击（非拖拽）
            const clickDuration = Date.now() - clickStartTime;
            if (!hasMoved && clickDuration < CLICK_THRESHOLD_TIME) {
                // 这是一个点击
                console.log(`[Interaction] 检测到点击（时长: ${clickDuration}ms）`);
                if (edgePeekStartedDrag) {
                    if (edgePeekStartedDragMode === 'widget') {
                        await this.restoreLive2DPeek('click-restore');
                    } else {
                        await this.restoreLive2DGameModeEdgePeek('click-restore');
                    }
                    return; // edge peek click restores instead of triggering touch motions
                }
                
                // 只在教程模式下，通过点击检测触发随机动画
                // 非教程模式下，通过 hit 事件处理
                await new Promise(resolve => setTimeout(resolve, 300));

                if(window.live2dManager.touchSetHitEventLock){
                    window.live2dManager.touchSetHitEventLock = false;
                }
                const customAreaId = this._lastPointerDownCustomTouchAreaId ||
                    (typeof this._getCustomTouchAreaIdAtPoint === 'function'
                        ? this._getCustomTouchAreaIdAtPoint(clickStartX, clickStartY)
                        : null);
                const hitAreas = this._lastTouchHitSeq === (this._lastTouchPointer && this._lastTouchPointer.seq)
                    && Array.isArray(this._lastTouchHitAreas)
                    ? this._lastTouchHitAreas
                    : [];
                const UseBlock = typeof window.live2dManager._getPreferredTouchSetHitArea === 'function'
                    ? window.live2dManager._getPreferredTouchSetHitArea(hitAreas, customAreaId)
                    : (customAreaId || "default");
                if (!window.live2dManager._canTriggerTouchSetArea(UseBlock)) return;
                await window.live2dManager._playTouchSetWithFallback(UseBlock);
                
                return; // 点击不需要保存位置
            }

            // 检测是否需要切换屏幕（多屏幕支持）
            // _checkAndSwitchDisplay returns true if a display switch occurred (and saved internally)
            const displaySwitched = await this._checkAndSwitchDisplay(model);

            // 如果没有发生屏幕切换，检测并执行自动吸附
            if (!displaySwitched) {
                await recordDragHintPointerEdgeRelease();
                // 执行自动吸附检测和动画
                const snapped = await this._checkAndPerformSnap(model);

                // 如果没有执行吸附，则正常保存位置
                if (!snapped) {
                    await this._savePositionAfterInteraction();
                }
                const widgetPeekApplied = await this._tryApplyLive2DPeek(model);
                if (!widgetPeekApplied) {
                    await this._tryApplyLive2DGameModeEdgePeek(model);
                }
                // 如果执行了吸附，_checkAndPerformSnap 内部会保存位置
            }
        }
    };

    const onDragMove = (event) => {
        if (!this._isModelReadyForInteraction) return;
        if (this._isDraggingModel) {
            if (typeof this.boostLinuxX11InteractiveFPS === 'function') {
                this.boostLinuxX11InteractiveFPS(1400);
            }
            if (isYuiGuideDragLocked()) {
                this._isDraggingModel = false;
                document.getElementById('live2d-canvas').style.cursor = '';
                restoreButtonPointerEvents();
                return;
            }

            // 再次检查是否变成多点触摸
            if (event.touches && event.touches.length > 1) {
                // 如果变成多点触摸，停止拖拽
                this._isDraggingModel = false;
                document.getElementById('live2d-canvas').style.cursor = '';
                // 【维护注意】所有退出拖拽的路径都必须调用 restoreButtonPointerEvents，
                //  否则 body 上的 neko-model-dragging class 不会被移除，按钮将永久失效。
                restoreButtonPointerEvents();
                return;
            }

            // 将 window 坐标转换为 Pixi 全局坐标 (通常在全屏下是一样的，但为了保险)
            // 这里假设 canvas 是全屏覆盖的
            const x = event.clientX;
            const y = event.clientY;
            dragHintLastPointer = captureDragHintPointer(event) || dragHintLastPointer;
            void recordDragHintPointerEdgeApproach();

            // 检测是否移动超过阈值
            const moveDistance = Math.sqrt(
                Math.pow(x - clickStartX, 2) + Math.pow(y - clickStartY, 2)
            );
            if (moveDistance > CLICK_THRESHOLD_DISTANCE) {
                hasMoved = true;
            }

            if (edgePeekStartedDrag && hasMoved && !edgePeekDragCleared) {
                if (edgePeekStartedDragMode === 'widget') {
                    this.clearLive2DPeek('drag-start', { restore: false });
                } else {
                    this.clearLive2DGameModeEdgePeek('drag-start', { restore: false });
                }
                dragStartPos.x = x - model.x;
                dragStartPos.y = y - model.y;
                edgePeekDragCleared = true;
            }

            model.x = x - dragStartPos.x;
            model.y = y - dragStartPos.y;
        }
    };

    // 清理旧的监听器
    if (this._dragEndListener) {
        window.removeEventListener('pointerup', this._dragEndListener);
        window.removeEventListener('pointercancel', this._dragEndListener);
    }
    if (this._dragMoveListener) {
        window.removeEventListener('pointermove', this._dragMoveListener);
    }

    // 保存新的监听器引用
    this._dragEndListener = onDragEnd;
    this._dragMoveListener = onDragMove;

    // 使用 window 监听拖拽结束和移动，确保即使移出 canvas 也能响应
    window.addEventListener('pointerup', onDragEnd);
    window.addEventListener('pointercancel', onDragEnd);
    window.addEventListener('pointermove', onDragMove);
};

// 设置滚轮缩放
Live2DManager.prototype.setupWheelZoom = function (model) {
    const isWheelPointOnCurrentModel = (event) => {
        const activeModel = this.currentModel || model;
        if (!activeModel || !event) return false;

        try {
            const view = this.pixi_app && this.pixi_app.view;
            const canvasRect = view && typeof view.getBoundingClientRect === 'function'
                ? view.getBoundingClientRect()
                : null;
            const rendererScreen = this.pixi_app && this.pixi_app.renderer
                ? this.pixi_app.renderer.screen
                : null;
            const rendererWidth = rendererScreen && Number.isFinite(rendererScreen.width)
                ? rendererScreen.width
                : 0;
            const rendererHeight = rendererScreen && Number.isFinite(rendererScreen.height)
                ? rendererScreen.height
                : 0;
            const scaleX = canvasRect && canvasRect.width > 0 && rendererWidth > 0
                ? rendererWidth / canvasRect.width
                : 1;
            const scaleY = canvasRect && canvasRect.height > 0 && rendererHeight > 0
                ? rendererHeight / canvasRect.height
                : 1;
            const x = canvasRect
                ? (event.clientX - canvasRect.left) * scaleX
                : event.clientX;
            const y = canvasRect
                ? (event.clientY - canvasRect.top) * scaleY
                : event.clientY;
            if (!Number.isFinite(x) || !Number.isFinite(y)) return false;

            const bounds = activeModel.getBounds();
            const left = Number.isFinite(bounds.left) ? bounds.left : bounds.x;
            const top = Number.isFinite(bounds.top) ? bounds.top : bounds.y;
            const width = Number.isFinite(bounds.width) ? bounds.width : (bounds.right - bounds.left);
            const height = Number.isFinite(bounds.height) ? bounds.height : (bounds.bottom - bounds.top);
            if (!Number.isFinite(left) || !Number.isFinite(top) || width <= 0 || height <= 0) return false;
            if (x < left || x > left + width || y < top || y > top + height) return false;

            try {
                if (typeof activeModel.hitTest === 'function') {
                    const hitAreas = activeModel.hitTest(x, y);
                    if (hitAreas && hitAreas.length > 0) return true;
                }
            } catch (_) {}

            const cx = left + width / 2;
            const cy = top + height / 2;
            const rx = width * 0.3;
            const ry = height * 0.45;
            if (rx <= 0 || ry <= 0) return false;
            const nx = (x - cx) / rx;
            const ny = (y - cy) / ry;
            return (nx * nx + ny * ny) <= 1;
        } catch (_) {
            return false;
        }
    };

    const onWheelScroll = (event) => {
        if (this.isLocked || !this.currentModel) return;
        if (this.isLive2DPeekActive()) {
            if (isWheelPointOnCurrentModel(event)) {
                event.preventDefault();
            }
            return; // edge peek ignores wheel zoom
        }
        if (this.isLive2DGameModeEdgePeekActive()) {
            if (isWheelPointOnCurrentModel(event)) {
                event.preventDefault();
            }
            return; // edge peek ignores wheel zoom
        }
        if (!isWheelPointOnCurrentModel(event)) return;
        event.preventDefault();

        // 根据 deltaY 大小动态计算缩放因子，避免固定倍率导致缩放过快
        // 鼠标滚轮通常 deltaY ≈ ±100，触控板 deltaY ≈ ±1~30
        const absDelta = Math.abs(event.deltaY);
        // 将 deltaY 映射到 0~0.08 的缩放增量（最大约 8%）
        const zoomStep = Math.min(absDelta / 1000, 0.08);
        const scaleFactor = 1 + zoomStep;

        const oldScale = this.currentModel.scale.x;
        let newScale = event.deltaY < 0 ? oldScale * scaleFactor : oldScale / scaleFactor;

        // 钳制缩放下限（MAX 暂不实施）
        newScale = Math.max(SCALE_LIMITS.MIN, newScale);

        this.currentModel.scale.set(newScale);

        // 缩放后触发分级恢复检测（含保存），替代原 _debouncedSavePosition
        this._debouncedSnapCheck();
    };

    const view = this.pixi_app.view;
    if (view.lastWheelListener) {
        view.removeEventListener('wheel', view.lastWheelListener);
    }
    view.addEventListener('wheel', onWheelScroll, { passive: false });
    view.lastWheelListener = onWheelScroll;
};

// 设置触摸缩放（双指捏合）
Live2DManager.prototype.setupTouchZoom = function (model) {
    const view = this.pixi_app.view;
    let initialDistance = 0;
    let initialScale = 1;
    let isTouchZooming = false;

    const getTouchDistance = (touch1, touch2) => {
        const dx = touch2.clientX - touch1.clientX;
        const dy = touch2.clientY - touch1.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    };

    const onTouchStart = (event) => {
        if (this.isLocked || !this.currentModel) return;
        if (this.isLive2DPeekActive()) {
            if (event.touches && event.touches.length === 2) {
                event.preventDefault();
            }
            isTouchZooming = false;
            return; // edge peek ignores touch zoom start
        }
        if (this.isLive2DGameModeEdgePeekActive()) {
            if (event.touches && event.touches.length === 2) {
                event.preventDefault();
            }
            isTouchZooming = false;
            return; // edge peek ignores touch zoom start
        }

        // 检测双指触摸
        if (event.touches.length === 2) {
            event.preventDefault();
            isTouchZooming = true;
            initialDistance = getTouchDistance(event.touches[0], event.touches[1]);
            initialScale = this.currentModel.scale.x;
        }
    };

    const onTouchMove = (event) => {
        if (this.isLocked || !this.currentModel || !isTouchZooming) return;
        if (this.isLive2DPeekActive()) {
            if (event.touches && event.touches.length === 2) {
                event.preventDefault();
            }
            isTouchZooming = false;
            return; // edge peek ignores touch zoom move
        }
        if (this.isLive2DGameModeEdgePeekActive()) {
            if (event.touches && event.touches.length === 2) {
                event.preventDefault();
            }
            isTouchZooming = false;
            return; // edge peek ignores touch zoom move
        }

        // 双指缩放
        if (event.touches.length === 2) {
            event.preventDefault();
            const currentDistance = getTouchDistance(event.touches[0], event.touches[1]);
            const scaleChange = currentDistance / initialDistance;
            let newScale = initialScale * scaleChange;

            // 限制缩放范围，与滚轮缩放保持一致
            newScale = Math.max(SCALE_LIMITS.MIN, Math.min(SCALE_LIMITS.MAX, newScale));

            this.currentModel.scale.set(newScale);
        }
    };

    const onTouchEnd = async (event) => {
        // 当手指数量小于2时，停止缩放
        if (event.touches.length < 2) {
            if (this.isLive2DPeekActive()) {
                isTouchZooming = false;
                return; // edge peek ignores touch zoom end without saving peek state
            }
            if (this.isLive2DGameModeEdgePeekActive()) {
                isTouchZooming = false;
                return; // edge peek ignores touch zoom end without saving peek state
            }
            if (isTouchZooming) {
                // 触摸缩放结束后自动保存位置和缩放
                await this._savePositionAfterInteraction();
            }
            isTouchZooming = false;
        }
    };

    // 移除旧的监听器（如果存在）
    if (view.lastTouchStartListener) {
        view.removeEventListener('touchstart', view.lastTouchStartListener);
    }
    if (view.lastTouchMoveListener) {
        view.removeEventListener('touchmove', view.lastTouchMoveListener);
    }
    if (view.lastTouchEndListener) {
        view.removeEventListener('touchend', view.lastTouchEndListener);
    }

    // 添加新的监听器
    view.addEventListener('touchstart', onTouchStart, { passive: false });
    view.addEventListener('touchmove', onTouchMove, { passive: false });
    view.addEventListener('touchend', onTouchEnd, { passive: false });

    // 保存监听器引用，便于清理
    view.lastTouchStartListener = onTouchStart;
    view.lastTouchMoveListener = onTouchMove;
    view.lastTouchEndListener = onTouchEnd;
};

// 启用鼠标跟踪以检测与模型的接近度
Live2DManager.prototype.enableMouseTracking = function (model, options = {}) {
    const { threshold = 70, HoverFadethreshold = 40 } = options; // 增加默认变淡阈值，从 5px 增加到 40px

    // 使用实例属性保存定时器，便于在其他地方访问
    if (this._hideButtonsTimer) {
        clearTimeout(this._hideButtonsTimer);
        this._hideButtonsTimer = null;
    }

    // 辅助函数：显示按钮
    const showButtons = () => {
        const lockIcon = document.getElementById('live2d-lock-icon');
        const floatingButtons = document.getElementById('live2d-floating-buttons');

        // 如果已经点击了"请她离开"，不显示锁按钮，但保持显示"请她回来"按钮
        if (this._goodbyeClicked) {
            if (lockIcon) {
                lockIcon.style.setProperty('display', 'none', 'important');
            }
            return;
        }

        // isFocusing 用于控制眼睛跟踪，悬浮菜单显示不受影响
        this.isFocusing = true;
        if (lockIcon) lockIcon.style.display = 'block';
        // 锁定状态下不显示浮动菜单
        if (floatingButtons && !this.isLocked) floatingButtons.style.display = 'flex';

        // 清除隐藏定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }
    };

    // 辅助函数：启动隐藏定时器
    const startHideTimer = (delay = 1000) => {
        const lockIcon = document.getElementById('live2d-lock-icon');
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        const hasOpenOverlay = () => {
            const popupUi = window.AvatarPopupUI || null;
            return !!(popupUi && typeof popupUi.hasVisibleOverlay === 'function' && popupUi.hasVisibleOverlay('live2d'));
        };
        const isPointerNearLock = () => {
            if (!lockIcon || lockIcon.style.display !== 'block') return false;
            const rect = lockIcon.getBoundingClientRect();
            const expandPx = 8;
            const localX = Number.isFinite(this._lastMouseLocalX) ? this._lastMouseLocalX : this._lastMouseX;
            const localY = Number.isFinite(this._lastMouseLocalY) ? this._lastMouseLocalY : this._lastMouseY;
            return isLive2DPointInRect({ x: localX, y: localY }, rect, expandPx);
        };
        const isPointerNearFloatingButtons = () => {
            if (!floatingButtons || floatingButtons.style.display === 'none') return false;
            const rect = floatingButtons.getBoundingClientRect();
            const localX = Number.isFinite(this._lastMouseLocalX) ? this._lastMouseLocalX : this._lastMouseX;
            const localY = Number.isFinite(this._lastMouseLocalY) ? this._lastMouseLocalY : this._lastMouseY;
            return isLive2DPointInRect({ x: localX, y: localY }, rect, 8);
        };

        if (this._goodbyeClicked) return;

        // 引导模式下不隐藏浮动按钮
        if (window.isInTutorial === true) return;

        // 如果已有定时器，不重复创建
        if (this._hideButtonsTimer) return;

        this._hideButtonsTimer = setTimeout(() => {
            // 引导模式下不隐藏
            if (window.isInTutorial === true) {
                this._hideButtonsTimer = null;
                return;
            }

            // 再次检查鼠标是否在按钮区域内
            if (this._isMouseOverButtons || isPointerNearLock() || isPointerNearFloatingButtons() || hasOpenOverlay()) {
                // 鼠标在按钮上，不隐藏，重新启动定时器
                this._hideButtonsTimer = null;
                startHideTimer(delay);
                return;
            }

            this.isFocusing = false;
            if (lockIcon) lockIcon.style.display = 'none';
            if (floatingButtons && !this._goodbyeClicked) {
                floatingButtons.style.display = 'none';
            }
            this._hideButtonsTimer = null;
        }, delay);
    };

    const live2dContainer = document.getElementById('live2d-container');
    let ctrlFadeActive = false;      // Ctrl 按住淡化
    let stationaryFadeActive = false; // 静止1秒淡化
    const applyFade = () => {
        if (!live2dContainer) return;
        const shouldFade = (ctrlFadeActive || stationaryFadeActive) && window.lockedHoverFadeEnabled !== false;
        live2dContainer.classList.toggle('locked-hover-fade', shouldFade);
    };

    // 监听锁定悬停淡化设置变更
    const onLockedHoverFadeChanged = () => {
        if (window.lockedHoverFadeEnabled === false) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
        }
    };
    if (this._lockedHoverFadeChangedListener) {
        window.removeEventListener('neko-locked-hover-fade-changed', this._lockedHoverFadeChangedListener);
    }
    this._lockedHoverFadeChangedListener = onLockedHoverFadeChanged;
    window.addEventListener('neko-locked-hover-fade-changed', onLockedHoverFadeChanged);

    // 跟踪 Ctrl 键状态（作为备用，主要从事件中直接读取）
    let isCtrlPressed = false;

    // 静止自动淡化：鼠标在模型范围内静止1秒后自动淡化
    this._stationaryFadeTimer = null;
    this._hasEnteredHoverRange = false; // 是否已进入过模型范围
    const STATIONARY_FADE_DELAY = 1000;

    const clearStationaryFadeTimer = () => {
        if (this._stationaryFadeTimer !== null) {
            clearTimeout(this._stationaryFadeTimer);
            this._stationaryFadeTimer = null;
        }
    };
    this._clearStationaryFadeTimer = clearStationaryFadeTimer;

    // 清理旧的键盘监听器（在添加新监听器之前）
    if (this._ctrlKeyDownListener) {
        window.removeEventListener('keydown', this._ctrlKeyDownListener);
    }
    if (this._ctrlKeyUpListener) {
        window.removeEventListener('keyup', this._ctrlKeyUpListener);
    }

    // 监听 Ctrl 键按下/释放事件（用于在鼠标不在窗口内时也能检测）
    const onKeyDown = (event) => {
        // 检查是否按下 Ctrl 或 Cmd 键
        if (event.ctrlKey || event.metaKey) {
            isCtrlPressed = true;
        }
    };

    const onKeyUp = (event) => {
        // 检查 Ctrl 或 Cmd 键是否释放
        if (!event.ctrlKey && !event.metaKey) {
            isCtrlPressed = false;
            // Ctrl 释放时重新计算淡化状态，让 stationaryFadeActive 有机会生效
            ctrlFadeActive = false;
            applyFade();
        }
    };

    // 添加全局键盘事件监听
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    // 保存监听器引用以便清理
    this._ctrlKeyDownListener = onKeyDown;
    this._ctrlKeyUpListener = onKeyUp;

    // 方法1：监听 PIXI 模型的 pointerover/pointerout 事件（适用于 Electron 透明窗口）
    model.on('pointerover', () => {
        showButtons();
        if (typeof this.boostLinuxX11InteractiveFPS === 'function') {
            this.boostLinuxX11InteractiveFPS();
        }
    });

    model.on('pointerout', () => {
        // 鼠标离开模型，启动隐藏定时器
        startHideTimer();
    });

    // 方法2：同时保留 window 的 pointermove 监听（适用于普通浏览器）
    const onPointerMove = (event) => {
        if (!this._isModelReadyForInteraction) return;
        // 更新 Ctrl 键状态：综合事件中的状态和本地状态
        // 如果是真实事件，更新本地状态；如果是模拟事件，本地状态保持不变（除非事件里带了 Ctrl）
        if (event.isTrusted) {
            isCtrlPressed = event.ctrlKey || event.metaKey;
        } else if (event.ctrlKey || event.metaKey) {
            // 如果模拟事件带了 Ctrl 键，也更新本地状态以供后续逻辑使用
            isCtrlPressed = true;
        }

        // 最终用于变淡判断的 Ctrl 状态
        const ctrlKeyPressed = event.ctrlKey || event.metaKey || isCtrlPressed;

        // 检查模型是否存在，防止切换模型时出现错误
        if (!model) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }

        // 检查模型是否已被销毁或不在舞台上
        if (model.destroyed || !model.parent || !this.pixi_app || !this.pixi_app.stage) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }
        
        // 检查当前模型是否仍然是传入的模型（防止模型切换后使用旧的模型引用）
        if (this.currentModel !== model) {
            // 模型已切换，清理监听器
            if (this._mouseTrackingListener) {
                window.removeEventListener('pointermove', this._mouseTrackingListener);
                this._mouseTrackingListener = null;
            }
            return;
        }
        
        // 检查模型是否仍在舞台上（防止模型被销毁或移除后仍然调用）
        if (!model.parent) {
            // 模型已被从舞台移除，清理监听器
            if (this._mouseTrackingListener) {
                window.removeEventListener('pointermove', this._mouseTrackingListener);
                this._mouseTrackingListener = null;
            }
            return;
        }
        
        // 检查模型是否已被销毁（检查关键属性是否存在）
        // 注意：某些PIXI版本可能没有destroyed属性，所以使用可选链
        if (model.destroyed === true) {
            return;
        }
        
        const pointerCoords = getLive2DNiriPetPointerCoordinates(event);
        const pointer = pointerCoords.virtual;
        const localPointer = pointerCoords.local;
        this._lastMouseX = pointer.x;
        this._lastMouseY = pointer.y;
        this._lastMouseLocalX = localPointer.x;
        this._lastMouseLocalY = localPointer.y;

        // 在拖拽期间不执行任何操作
        if ((model.interactive && model.dragging) || this._isDraggingModel) {
            return;
        }
        // 如果已经点击了"请她离开"，特殊处理
        if (this._goodbyeClicked) {
            const lockIcon = document.getElementById('live2d-lock-icon');
            const floatingButtons = document.getElementById('live2d-floating-buttons');

            if (lockIcon) {
                lockIcon.style.setProperty('display', 'none', 'important');
            }
            // goodbye 状态下这里只维护锁图标/浮动按钮可见性。
            // 返回球必须由 app-ui 在完成定位后再显示，避免先以默认 (0, 0) 闪现。
            if (floatingButtons) {
                floatingButtons.style.display = 'none';
            }
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }

        try {
            // 在调用 getBounds 前再次检查模型是否有效
            if (!model.parent || model.destroyed) {
                return;
            }
            const bounds = model.getBounds();

            // 使用椭圆近似检测（基于完整模型边界，椭圆可以部分在屏幕外）
            const centerX = (bounds.left + bounds.right) / 2;
            const centerY = (bounds.top + bounds.bottom) / 2;
            const width = bounds.right - bounds.left;
            const height = bounds.bottom - bounds.top;

            let distance;
            // 防止除零：当宽度或高度接近零时，回退到矩形距离计算
            if (width < 1 || height < 1) {
                const dx = Math.max(bounds.left - pointer.x, 0, pointer.x - bounds.right);
                const dy = Math.max(bounds.top - pointer.y, 0, pointer.y - bounds.bottom);
                distance = Math.sqrt(dx * dx + dy * dy);
            } else {
                // 椭圆半径比例（相对于边界框）
                const ellipseRadiusX = width * 0.3;
                const ellipseRadiusY = height * 0.45;

                // 计算点到椭圆的归一化距离
                const normalizedX = (pointer.x - centerX) / ellipseRadiusX;
                const normalizedY = (pointer.y - centerY) / ellipseRadiusY;
                const ellipseDistance = Math.sqrt(normalizedX * normalizedX + normalizedY * normalizedY);

                // 将椭圆距离转换为像素距离（用于阈值比较）
                // ellipseDistance <= 1 表示在椭圆内部，distance = 0
                // ellipseDistance > 1 表示在椭圆外部，distance 为超出椭圆边缘的等效像素距离
                distance = ellipseDistance <= 1 ? 0 : (ellipseDistance - 1) * Math.min(ellipseRadiusX, ellipseRadiusY);
            }

            // 检查是否启用了全屏跟踪
            const isFullscreenTracking = this.isFullscreenTrackingEnabled ? this.isFullscreenTrackingEnabled() : false;

            // 额外检查：鼠标必须在模型可见区域附近（除非启用全屏跟踪）
            const isPointerNearVisibleModel = pointer.x >= bounds.left - threshold && pointer.x <= bounds.right + threshold &&
                                              pointer.y >= Math.max(bounds.top, 0) - threshold && pointer.y <= Math.min(bounds.bottom, window.innerHeight) + threshold;

            // 如果鼠标不在屏幕内或不在模型可见区域附近，且未启用全屏跟踪，则视为远离模型
            if (!isPointerNearVisibleModel && !isFullscreenTracking) {
                this.isFocusing = false;
                startHideTimer();
                clearStationaryFadeTimer();
                ctrlFadeActive = false;
                stationaryFadeActive = false;
                applyFade();
                return;
            }

            const isNearModel = distance < HoverFadethreshold;

            // 鼠标在 UI 元素（锁图标 / 浮动按钮）上时，重置淡化状态，
            // 防止离开 UI 后残留的 stationaryFadeActive 立即重新触发淡化
            const live2dLockIcon = document.getElementById('live2d-lock-icon');
            const live2dFloatingBtns = document.getElementById('live2d-floating-buttons');
            let isOverUi = false;
            if (live2dLockIcon && live2dLockIcon.style.display !== 'none') {
                const lr = live2dLockIcon.getBoundingClientRect();
                if (isLive2DPointInRect(localPointer, lr, 0)) isOverUi = true;
            }
            if (!isOverUi && live2dFloatingBtns && live2dFloatingBtns.style.display !== 'none') {
                const br = live2dFloatingBtns.getBoundingClientRect();
                if (isLive2DPointInRect(localPointer, br, 0)) isOverUi = true;
            }
            if (isOverUi) {
                clearStationaryFadeTimer();
                ctrlFadeActive = false;
                stationaryFadeActive = false;
                this._hasEnteredHoverRange = false;
                applyFade();
            }

            // 静止时启动定时器，移出范围时清除（移动端无鼠标悬停，跳过）
            const isMobileDevice = (window.appUtils && typeof window.appUtils.isMobile === 'function' && window.appUtils.isMobile()) || /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
            if (!isMobileDevice && this.isLocked && isNearModel && !isOverUi) {
                // 首次进入范围：设置标志并启动定时器
                if (!this._hasEnteredHoverRange) {
                    this._hasEnteredHoverRange = true;
                    if (this._stationaryFadeTimer === null && !stationaryFadeActive) {
                        this._stationaryFadeTimer = setTimeout(() => {
                            stationaryFadeActive = true;
                            applyFade();
                        }, STATIONARY_FADE_DELAY);
                    }
                }
                // 已在范围内：移动时不重启定时器，只更新位置
            } else {
                // 移出范围：清除定时器并重置标志
                if (this._stationaryFadeTimer !== null || stationaryFadeActive) {
                    clearStationaryFadeTimer();
                    stationaryFadeActive = false;
                    applyFade();
                }
                this._hasEnteredHoverRange = false;
            }

            // Ctrl 淡化：锁定 + Ctrl + 在模型范围内（独立于静止淡化，移动端跳过，UI 上时跳过）
            ctrlFadeActive = !isMobileDevice && this.isLocked && ctrlKeyPressed && isNearModel && !isOverUi;
            applyFade();

            const canvasEl = document.getElementById('live2d-canvas');
            const isYuiGuideFaceForwardLocked = window.nekoYuiGuideFaceForwardLock === true
                && window.nekoYuiGuideIntroVoiceLookAtActive !== true;
            const centerYuiGuideLookAt = () => {
                if (model.internalModel && model.internalModel.focusController) {
                    const fc = model.internalModel.focusController;
                    fc.targetX = 0;
                    fc.targetY = 0;
                    if (Number.isFinite(Number(fc.x))) fc.x = 0;
                    if (Number.isFinite(Number(fc.y))) fc.y = 0;
                }
                const coreModel = model.internalModel && model.internalModel.coreModel;
                if (coreModel && typeof coreModel.setParameterValueById === 'function') {
                    try {
                        coreModel.setParameterValueById('ParamAngleX', 0);
                        coreModel.setParameterValueById('ParamAngleY', 0);
                        coreModel.setParameterValueById('ParamEyeBallX', 0);
                        coreModel.setParameterValueById('ParamEyeBallY', 0);
                    } catch (_) {}
                }
            };

            if (distance < threshold) {
                if (typeof this.boostLinuxX11InteractiveFPS === 'function') {
                    this.boostLinuxX11InteractiveFPS();
                }
                showButtons();
                if (canvasEl && !this.isLocked && !(model.interactive && model.dragging)) {
                    // hitTest + 椭圆内部判定（0.3w × 0.45h），不外扩
                    let isOnModel = false;
                    try {
                        const hitAreas = model.hitTest(pointer.x, pointer.y);
                        if (hitAreas && hitAreas.length > 0) isOnModel = true;
                    } catch (_) {}
                    if (!isOnModel) isOnModel = distance === 0;
                    canvasEl.style.cursor = isOnModel ? 'grab' : '';
                }
                const isMouseTrackingEnabled = this.isMouseTrackingEnabled ? this.isMouseTrackingEnabled() : (window.mouseTrackingEnabled !== false);
                if (this.isFocusing) {
                    if (isYuiGuideFaceForwardLocked) {
                        centerYuiGuideLookAt();
                    } else if (isMouseTrackingEnabled) {
                        model.focus(pointer.x, pointer.y);
                    } else {
                        if (model.internalModel && model.internalModel.focusController) {
                            const fc = model.internalModel.focusController;
                            fc.targetX = 0;
                            fc.targetY = 0;
                        }
                    }
                }
            } else if (isFullscreenTracking) {
                if (typeof this.boostLinuxX11InteractiveFPS === 'function') {
                    this.boostLinuxX11InteractiveFPS();
                }
                if (canvasEl && !this.isLocked && !(model.interactive && model.dragging)) {
                    canvasEl.style.cursor = 'grab';
                }
                const isMouseTrackingEnabled = this.isMouseTrackingEnabled ? this.isMouseTrackingEnabled() : (window.mouseTrackingEnabled !== false);
                if (isYuiGuideFaceForwardLocked) {
                    centerYuiGuideLookAt();
                } else if (isMouseTrackingEnabled) {
                    model.focus(pointer.x, pointer.y);
                } else {
                    if (model.internalModel && model.internalModel.focusController) {
                        const fc = model.internalModel.focusController;
                        fc.targetX = 0;
                        fc.targetY = 0;
                    }
                }
            } else {
                this.isFocusing = false;
                if (canvasEl && !(model.interactive && model.dragging)) {
                    canvasEl.style.cursor = '';
                }
                startHideTimer();
            }
        } catch (error) {
            // 静默处理错误，避免控制台刷屏
            // 只在开发模式下输出详细错误信息
            if (window.DEBUG || window.location.hostname === 'localhost') {
                console.error('Live2D 交互错误:', error);
            }
        }
    };

    // 窗口失去焦点时，只重置淡化效果，不重置 Ctrl 键状态
    // 这样窗口重新获得焦点后，如果 Ctrl 仍被按住，淡化功能可以恢复
    const onBlur = () => {
        // blur 时 Ctrl 键事件无法到达，必须主动清除 Ctrl 状态
        isCtrlPressed = false;
        ctrlFadeActive = false;
        clearStationaryFadeTimer();
        // blur 时清除定时器和淡化状态，焦点恢复后需重新触发
        if (stationaryFadeActive) {
            stationaryFadeActive = false;
        }
        applyFade();
        this._hasEnteredHoverRange = false;
    };

    // 清理旧的监听器
    if (this._mouseTrackingListener) {
        window.removeEventListener('pointermove', this._mouseTrackingListener);
    }
    if (this._windowBlurListener) {
        window.removeEventListener('blur', this._windowBlurListener);
    }

    // 保存新的监听器引用
    this._mouseTrackingListener = onPointerMove;
    this._windowBlurListener = onBlur;

    // 使用 window 监听鼠标移动和窗口失去焦点
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('blur', onBlur);

    // 监听浮动按钮容器的鼠标进入/离开事件
    // 延迟设置，因为按钮容器可能还没创建
    setTimeout(() => {
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            floatingButtons.addEventListener('mouseenter', () => {
                this._isMouseOverButtons = true;
                // 鼠标进入按钮区域，清除隐藏定时器
                if (this._hideButtonsTimer) {
                    clearTimeout(this._hideButtonsTimer);
                    this._hideButtonsTimer = null;
                }
            });

            floatingButtons.addEventListener('mouseleave', () => {
                this._isMouseOverButtons = false;
                // 鼠标离开按钮区域，启动隐藏定时器
                startHideTimer();
            });
        }

        // 同样处理锁图标
        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.addEventListener('mouseenter', () => {
                this._isMouseOverButtons = true;
                if (this._hideButtonsTimer) {
                    clearTimeout(this._hideButtonsTimer);
                    this._hideButtonsTimer = null;
                }
            });

            lockIcon.addEventListener('mouseleave', () => {
                this._isMouseOverButtons = false;
                startHideTimer();
            });
        }
    }, 100);
};

Live2DManager.prototype._restoreClickEffectState = async function(options = {}) {
    const restoreIdle = options && options.restoreIdle === true;
    const expectedClickEffectId = options ? options.clickEffectId : null;
    const expectedRestoreToken = options ? options.restoreToken : null;
    const hasExpectedClickEffectId = expectedClickEffectId !== null && expectedClickEffectId !== undefined;
    const hasExpectedRestoreToken = expectedRestoreToken !== null && expectedRestoreToken !== undefined;

    const isCurrentRestore = () => {
        if (hasExpectedRestoreToken && this._clickEffectRestoreToken !== expectedRestoreToken) {
            return false;
        }
        if (hasExpectedClickEffectId && this._currentClickEffectId !== expectedClickEffectId) {
            return false;
        }
        return true;
    };

    const finishClickEffectRestore = () => {
        if (!isCurrentRestore()) {
            return false;
        }
        this._currentClickEffectId = null;
        this._clickEffectMotion = null;
        return true;
    };

    if (!isCurrentRestore()) {
        return false;
    }

    if (this._clickEffectMotion && typeof this._clickEffectMotion.stop === 'function') {
        try { this._clickEffectMotion.stop(); } catch (_) {}
    }
    this._clickEffectMotion = null;

    const restoreIdleMotion = async () => {
        if (!restoreIdle || typeof window.restoreLive2DIdleAnimationOnMainPage !== 'function') {
            return true;
        }
        if (!isCurrentRestore()) {
            return false;
        }
        try {
            await window.restoreLive2DIdleAnimationOnMainPage({ shouldContinue: isCurrentRestore });
            return isCurrentRestore();
        } catch (e) {
            console.warn('[ClickEffect] 恢复待机动作失败:', e);
            return false;
        }
    };

    try {
        if (typeof this.smoothResetToInitialState === 'function') {
            await this.smoothResetToInitialState();
            if (!isCurrentRestore()) {
                return false;
            }
            await restoreIdleMotion();
            return finishClickEffectRestore();
        }
    } catch (e) {
        console.warn('[ClickEffect] 平滑恢复失败，回退到即时恢复:', e);
    }

    if (!isCurrentRestore()) {
        return false;
    }

    try {
        if (typeof this.clearExpression === 'function') {
            this.clearExpression();
        }
    } catch (e) {
        console.warn('[ClickEffect] 清除表情失败:', e);
    }
    await restoreIdleMotion();
    return finishClickEffectRestore();
};

/**
 * 播放临时点击效果（低优先级，会自动恢复）
 * @param {string} emotion - 情感名称
 * @param {number} priority - 动作优先级 (1=IDLE, 2=NORMAL, 3=FORCE)
 * @param {number} duration - 效果持续时间（毫秒）
 */
Live2DManager.prototype._playTemporaryClickEffect = async function(emotion, priority = 1, duration = 3000) {
    const triggerLog = {
        emotion,
        priority,
        durationMs: duration,
        motionCandidates: 0,
        expressionCandidates: 0,
        motions: [],
        expressions: [],
        failedMotions: [],
        failedExpressions: []
    };
    if (!this.currentModel) {
        console.warn('[ClickEffect] 无法播放：模型未加载');
        triggerLog.reason = 'model_not_loaded';
        logLive2DClickTriggerSummary('ClickEffect', triggerLog);
        return false;
    }
    let didPlayEffect = false;
    let clickEffectId = null;
    const previousClickEffectId = this._currentClickEffectId;
    const hadClickEffectState = Boolean(
        previousClickEffectId ||
        this._clickEffectRestoreTimer ||
        this._clickEffectMotion
    );
    this._clickEffectRestoreToken = (this._clickEffectRestoreToken || 0) + 1;
    const restoreToken = this._clickEffectRestoreToken;
    // 跨 await 校验本次点击是否仍是当前 attempt：被更新的点击接管后立刻让出共享状态
    const isCurrentPlayAttempt = () => this._clickEffectRestoreToken === restoreToken;

    // 清除之前的点击效果恢复定时器
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }

    if (typeof this._cancelSmoothReset === 'function') {
        this._cancelSmoothReset();
    }
    
    if (this._clickEffectMotion && typeof this._clickEffectMotion.stop === 'function') {
        try { this._clickEffectMotion.stop(); } catch (e) {}
    }
    this._clickEffectMotion = null;

    try {
        // 准备表情兜底：动作不可用或播放失败时才播放
        let expressionFiles = [];
        if (this.emotionMapping && this.emotionMapping.expressions && this.emotionMapping.expressions[emotion]) {
            expressionFiles = this.emotionMapping.expressions[emotion];
        }
        
        // 兼容旧结构：按 emotion 前缀匹配
        if (expressionFiles.length === 0 && this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
            const candidates = this.fileReferences.Expressions.filter(e => (e.Name || '').startsWith(emotion));
            expressionFiles = candidates.map(e => e.File).filter(Boolean);
        }

        // 最终兜底：如果仍然没有匹配到，使用全部可用表情随机播放
        if (expressionFiles.length === 0 && this.fileReferences && Array.isArray(this.fileReferences.Expressions) && this.fileReferences.Expressions.length > 0) {
            expressionFiles = this.fileReferences.Expressions.map(e => e.File).filter(Boolean);
        }

        // 跳过已确认失效的 expression，避免每次点击都重复 404
        if (expressionFiles.length > 0 && typeof this.isExpressionFileMissing === 'function') {
            expressionFiles = expressionFiles.filter(file => !this.isExpressionFileMissing(file));
        }
        triggerLog.expressionCandidates = expressionFiles.length;

        // 1. 优先播放低优先级动作
        let motions = null;
        let motionGroup = emotion; // 用于 this.currentModel.motion(group, index, priority)
        if (this.fileReferences && this.fileReferences.Motions && this.fileReferences.Motions[emotion]) {
            motions = this.fileReferences.Motions[emotion];
        } else if (this.emotionMapping && this.emotionMapping.motions && this.emotionMapping.motions[emotion]) {
            const emotionMotions = this.emotionMapping.motions[emotion];
            if (Array.isArray(emotionMotions) && emotionMotions.length > 0) {
                if (typeof emotionMotions[0] === 'string') {
                    motions = emotionMotions.map(f => ({ File: f }));
                } else {
                    motions = emotionMotions;
                }
            }
        }

        // 兜底：emotion 对不上任何 motion group 时，从所有可用 group 随机选一个
        // 优先非 PreviewAll 分组；若仅 PreviewAll 有 motion（服务端注入的常见情况）则退而用它
        if ((!motions || motions.length === 0) && this.fileReferences && this.fileReferences.Motions) {
            const hasUsableMotions = (g) => Array.isArray(this.fileReferences.Motions[g]) && this.fileReferences.Motions[g].length > 0;
            const allGroups = Object.keys(this.fileReferences.Motions);
            const nonPreviewGroups = allGroups.filter(g => g !== 'PreviewAll' && hasUsableMotions(g));
            const fallbackGroups = nonPreviewGroups.length > 0
                ? nonPreviewGroups
                : allGroups.filter(hasUsableMotions);
            if (fallbackGroups.length > 0) {
                motionGroup = fallbackGroups[Math.floor(Math.random() * fallbackGroups.length)];
                motions = this.fileReferences.Motions[motionGroup];
            }
        }
        triggerLog.motionCandidates = Array.isArray(motions) ? motions.length : 0;

        if (motions && motions.length > 0) {
            // 使用低优先级播放动作
            // pixi-live2d-display 的 motion(group, index, priority) 支持优先级参数
            try {
                const motion = await this.currentModel.motion(motionGroup, undefined, priority);
                if (!isCurrentPlayAttempt()) {
                    // 已被新的点击接管：停掉本次刚启动的动作，避免后台占用，并放弃写共享状态
                    if (motion && typeof motion.stop === 'function') {
                        try { motion.stop(); } catch (_) {}
                    }
                    triggerLog.reason = 'superseded_after_motion';
                    return false;
                }
                if (motion) {
                    console.log(`[ClickEffect] 播放临时动作: ${motionGroup}（优先级: ${priority}）`);
                    this._clickEffectMotion = motion;
                    triggerLog.motions.push({
                        group: motionGroup,
                        selection: 'random',
                        priority,
                        candidateCount: motions.length
                    });
                    didPlayEffect = true;
                } else {
                    triggerLog.failedMotions.push({
                        group: motionGroup,
                        selection: 'random',
                        priority,
                        reason: 'motion_returned_falsy'
                    });
                }
            } catch (motionError) {
                triggerLog.failedMotions.push({
                    group: motionGroup,
                    selection: 'random',
                    priority,
                    reason: motionError?.message || String(motionError)
                });
                console.warn('[ClickEffect] 动作播放失败:', motionError);
            }
        }

        // 2. 动作不可用或播放失败时，再用表情兜底
        if (!didPlayEffect && expressionFiles.length > 0) {
            const choiceFile = this.getRandomElement(expressionFiles);
            if (choiceFile && typeof this.playExpression === 'function') {
                console.log(`[ClickEffect] 播放临时表情: ${choiceFile}`);
                const expressionPlayed = await this.playExpression(emotion, choiceFile);
                if (!isCurrentPlayAttempt()) {
                    // 已被新的点击接管，不要继续写共享状态
                    triggerLog.reason = 'superseded_after_expression';
                    return false;
                }
                if (expressionPlayed !== false) {
                    triggerLog.expressions.push({ emotion, file: choiceFile, fallbackFor: 'motion' });
                    didPlayEffect = true;
                } else {
                    triggerLog.failedExpressions.push({ emotion, file: choiceFile, reason: 'play_returned_false' });
                    console.warn(`[ClickEffect] 临时表情播放失败: ${choiceFile}`);
                }
            }
        } else if (!didPlayEffect) {
            console.log("[ClickEffect] 没找到可用表情")
        }

        if (!didPlayEffect) {
            triggerLog.reason = triggerLog.reason || 'nothing_played';
            console.log('[ClickEffect] 没有可播放的点击表情或动作，保持当前状态');
            if (hadClickEffectState && previousClickEffectId && this._currentClickEffectId === previousClickEffectId) {
                await this._restoreClickEffectState({
                    restoreIdle: true,
                    clickEffectId: previousClickEffectId,
                    restoreToken
                });
            }
            return false;
        }

        if (!isCurrentPlayAttempt()) {
            // 走到这里说明 await 之间被新的点击接管了；不要再注册我们自己的恢复定时器
            triggerLog.reason = 'superseded_before_restore_timer';
            return false;
        }

        // 3. 设置恢复定时器
        // 使用唯一 ID 标记此次点击效果，用于判断是否应该恢复
        this._clickEffectIdSeq = (this._clickEffectIdSeq || 0) + 1;
        clickEffectId = this._clickEffectIdSeq;
        this._currentClickEffectId = clickEffectId;
        
        this._clickEffectRestoreTimer = setTimeout(() => {
            this._clickEffectRestoreTimer = null;

            // 检查是否仍然是此次点击效果（没有被新的情感/点击覆盖）
            if (this._currentClickEffectId !== clickEffectId) {
                console.log('[ClickEffect] 临时效果已被新的情感覆盖，跳过恢复');
                return;
            }

            console.log('[ClickEffect] 临时效果结束，平滑恢复到默认状态并恢复待机动作');
            // 复用统一恢复入口：smoothReset/clearExpression + restoreLive2DIdleAnimationOnMainPage
            // 与外层 triggerRandomEmotion 的恢复路径保持对偶，避免成功点击后丢失 saved idle motion
            this._restoreClickEffectState({ restoreIdle: true, clickEffectId, restoreToken }).catch(e => {
                console.warn('[ClickEffect] 恢复点击效果状态失败:', e);
            });
        }, duration);

        console.log(`[ClickEffect] 临时效果将在 ${duration}ms 后恢复`);
        return true;

    } catch (error) {
        triggerLog.reason = triggerLog.reason || 'exception';
        console.error('[ClickEffect] 播放临时效果失败:', error);
        const restoreClickEffectId = clickEffectId || previousClickEffectId;
        if (restoreClickEffectId && this._currentClickEffectId === restoreClickEffectId) {
            await this._restoreClickEffectState({
                restoreIdle: true,
                clickEffectId: restoreClickEffectId,
                restoreToken
            });
        }
        return false;
    } finally {
        logLive2DClickTriggerSummary('ClickEffect', triggerLog);
    }
};

// 交互后保存位置和缩放的辅助函数
Live2DManager.prototype._savePositionAfterInteraction = async function () {
    if (!this.currentModel || !this._lastLoadedModelPath) {
        console.debug('无法保存位置：模型或路径未设置');
        return;
    }

    if (typeof this.recoverRendererFromReturnBallViewport === 'function') {
        try {
            this.recoverRendererFromReturnBallViewport('save-position-before');
        } catch (error) {
            console.warn('[Live2D Interaction] 恢复 return-ball viewport 失败，继续保存位置:', error);
        }
    }

    const position = { x: this.currentModel.x, y: this.currentModel.y };
    const scale = { x: this.currentModel.scale.x, y: this.currentModel.scale.y };

    // 验证数据有效性
    if (!Number.isFinite(position.x) || !Number.isFinite(position.y) ||
        !Number.isFinite(scale.x) || !Number.isFinite(scale.y)) {
        console.warn('位置或缩放数据无效，跳过保存');
        return;
    }

    // 获取当前窗口所在显示器的信息（用于多屏幕位置恢复）
    let displayInfo = null;
    if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
        try {
            const currentDisplay = await window.electronScreen.getCurrentDisplay();
            console.debug('currentDisplay', currentDisplay);
            if (currentDisplay) {
                // 优先使用 screenX/screenY，兜底使用 bounds.x/bounds.y
                let screenX = currentDisplay.screenX;
                let screenY = currentDisplay.screenY;

                // 如果 screenX/screenY 不存在，尝试从 bounds 获取
                if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                    if (currentDisplay.bounds &&
                        Number.isFinite(currentDisplay.bounds.x) &&
                        Number.isFinite(currentDisplay.bounds.y)) {
                        screenX = currentDisplay.bounds.x;
                        screenY = currentDisplay.bounds.y;
                        console.debug('使用 bounds 作为显示器位置');
                    }
                }

                if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                    displayInfo = {
                        screenX: screenX,
                        screenY: screenY
                    };
                    console.debug('保存显示器位置:', displayInfo);
                }
            }
        } catch (error) {
            console.warn('获取显示器信息失败:', error);
        }
    }

    // 使用渲染器逻辑尺寸作为归一化基准（renderer 不再自动 resize，尺寸与稳定屏幕分辨率等价）
    let viewportInfo = null;
    if (this.pixi_app && this.pixi_app.renderer) {
        const rw = this.pixi_app.renderer.screen.width;
        const rh = this.pixi_app.renderer.screen.height;
        if (Number.isFinite(rw) && Number.isFinite(rh) && rw > 0 && rh > 0) {
            viewportInfo = { width: rw, height: rh };
        }
    }
    // 异步保存，不阻塞交互
    this.saveUserPreferences(this._lastLoadedModelPath, position, scale, null, displayInfo, viewportInfo)
        .then(success => {
            if (success) {
                console.debug('模型位置和缩放已自动保存');
            } else {
                console.warn('自动保存位置失败');
            }
        })
        .catch(error => {
            console.error('自动保存位置时出错:', error);
        });
};

// 防抖动保存位置的辅助函数（用于滚轮缩放等连续操作）
Live2DManager.prototype._debouncedSavePosition = function () {
    // 清除之前的定时器
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
    }

    // 设置新的定时器，500ms后保存
    this._savePositionDebounceTimer = setTimeout(() => {
        this._savePositionAfterInteraction().catch(error => {
            // 错误已在 _savePositionAfterInteraction 内部记录，这里只是确保 Promise 被处理
            console.error('防抖动保存位置时出错:', error);
        });
    }, 500);
};

// 防抖分级恢复检测（用于滚轮缩放后的边界检查 + 位置保存）
Live2DManager.prototype._debouncedSnapCheck = function () {
    if (this._snapCheckTimer) clearTimeout(this._snapCheckTimer);
    // 同时取消可能残留的保存定时器，避免在吸附动画完成前保存中间状态
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
    }
    this._snapCheckTimer = setTimeout(async () => {
        if (!this.currentModel || this._isSnapping) return;

        // 统一复用现有吸附流程（含守卫、动画、保存）
        // _checkSnapRequired 会根据 overflow 方向计算最近边缘，
        // 无论模型是部分出界还是完全消失都能正确处理
        const snapped = await this._checkAndPerformSnap(this.currentModel);
        if (!snapped) {
            // 未触发吸附（模型在合理范围内），仅保存缩放后的位置
            await this._savePositionAfterInteraction();
        }
    }, 300);  // 300ms 防抖，等待连续滚轮操作结束
};

// 多屏幕支持：检测模型是否移出当前屏幕并切换到新屏幕
// Returns true if a display switch occurred (and position was saved internally), false otherwise
Live2DManager.prototype._checkAndSwitchDisplay = async function (model) {
    // 仅在 Electron 环境下执行
    if (!window.electronScreen || !window.electronScreen.moveWindowToDisplay) {
        return false;
    }

    try {
        // 获取模型中心点的窗口坐标
        const bounds = model.getBounds();
        const modelCenterX = (bounds.left + bounds.right) / 2;
        const modelCenterY = (bounds.top + bounds.bottom) / 2;

        // 获取所有屏幕信息
        const displays = await window.electronScreen.getAllDisplays();
        if (!displays || displays.length <= 1) {
            // 只有一个屏幕，不需要切换
            return false;
        }

        // 检查模型是否在当前窗口范围内
        const windowWidth = window.innerWidth;
        const windowHeight = window.innerHeight;

        // 如果模型大部分还在当前窗口内，不切换
        if (modelCenterX >= 0 && modelCenterX < windowWidth &&
            modelCenterY >= 0 && modelCenterY < windowHeight) {
            return false;
        }

        // 模型移出了当前窗口，查找目标屏幕
        // 需要转换为屏幕坐标（相对于屏幕的绝对坐标）

        // 首先获取当前窗口所在的显示器
        const currentDisplay = await window.electronScreen.getCurrentDisplay();
        if (!currentDisplay) {
            console.warn('[Live2D] 无法获取当前显示器信息');
            return false;
        }

        // 计算当前窗口左上角在屏幕上的绝对位置
        const windowScreenX = currentDisplay.screenX;
        const windowScreenY = currentDisplay.screenY;

        // 计算模型中心点的屏幕绝对坐标
        const modelScreenX = windowScreenX + modelCenterX;
        const modelScreenY = windowScreenY + modelCenterY;

        // 遍历所有显示器，找到包含模型中心点的显示器
        let targetDisplay = null;
        for (const display of displays) {
            // 检查模型中心点是否在这个显示器内
            if (modelScreenX >= display.screenX &&
                modelScreenX < display.screenX + display.width &&
                modelScreenY >= display.screenY &&
                modelScreenY < display.screenY + display.height) {
                targetDisplay = display;
                break;
            }
        }

        if (targetDisplay) {
            console.log('[Live2D] 检测到模型移出当前屏幕，准备切换到屏幕:', targetDisplay.id);

            // 切换期间屏蔽常规吸附，防止中间态用旧窗口尺寸做 clamp 导致误吸附
            this._pendingDisplaySwitch = true;
            try {
                // 使用之前已经计算好的模型屏幕绝对坐标调用切换屏幕
                const result = await window.electronScreen.moveWindowToDisplay(modelScreenX, modelScreenY);

                if (result && result.success && !result.sameDisplay) {
                    console.log('[Live2D] 屏幕切换成功:', result);

                    // 计算模型在新窗口中的位置
                    // 新窗口左上角是 targetDisplay.screenX, targetDisplay.screenY
                    // 模型新的窗口坐标 = 模型屏幕坐标 - 新窗口屏幕坐标
                    const newModelX = modelScreenX - targetDisplay.screenX;
                    const newModelY = modelScreenY - targetDisplay.screenY;

                    // 考虑缩放因子变化
                    if (result.scaleRatio && result.scaleRatio !== 1) {
                        // 如果不同屏幕有不同的缩放，可能需要调整模型大小
                        // 但通常保持模型原大小更合理，只调整位置
                        console.log('[Live2D] 屏幕缩放比变化:', result.scaleRatio);
                    }

                    // 从中心点转换到锚点位置
                    // newModelX/newModelY 是模型视觉中心的坐标
                    // PIXI 的 x/y 是锚点位置，需要根据锚点偏离中心的距离调整
                    model.x = newModelX + (model.anchor.x - 0.5) * model.width * model.scale.x;
                    model.y = newModelY + (model.anchor.y - 0.5) * model.height * model.scale.y;

                    console.log('[Live2D] 模型新位置:', model.x, model.y);

                    // 屏幕切换后，延迟两帧再检测是否需要吸附
                    // 两帧：一帧给 setBounds 落地，一帧给 resize 事件刷新 innerWidth/Height
                    await new Promise(resolve => requestAnimationFrame(resolve));
                    await new Promise(resolve => requestAnimationFrame(resolve));

                    // 检测并执行自动吸附（切换到新屏幕后模型可能仍超出边界）
                    // 屏幕切换后使用更宽松的吸附条件（只要超出就吸附）
                    const snapped = await this._checkAndPerformSnap(model, { afterDisplaySwitch: true });

                    // 如果没有执行吸附，保存位置
                    if (!snapped) {
                        await this._savePositionAfterInteraction();
                    }
                    // 如果执行了吸附，_checkAndPerformSnap 内部会保存位置
                    if (window.NekoAvatarMultiScreenDragHint &&
                        typeof window.NekoAvatarMultiScreenDragHint.markDisplaySwitchSuccess === 'function') {
                        window.NekoAvatarMultiScreenDragHint.markDisplaySwitchSuccess('live2d');
                    }

                    return true;  // Display switch occurred
                }
            } finally {
                this._pendingDisplaySwitch = false;
            }
        }
        return false;  // No display switch occurred
    } catch (error) {
        this._pendingDisplaySwitch = false;
        console.error('[Live2D] 检测/切换屏幕时出错:', error);
        return false;
    }
};

// setupResizeSnapDetection 已移除：渲染器仅在真实屏幕分辨率变化时 resize，不再需要吸附检测

/**
 * 手动触发吸附检测（供外部调用）
 * @returns {Promise<boolean>} 是否执行了吸附
 */
Live2DManager.prototype.snapToScreen = async function () {
    if (!this.currentModel) {
        console.warn('[Live2D] 无法执行吸附：模型未加载');
        return false;
    }

    return await this._checkAndPerformSnap(this.currentModel);
};

/**
 * 更新吸附配置
 * @param {Object} config - 配置对象
 * @param {number} [config.threshold] - 吸附阈值（像素）
 * @param {number} [config.margin] - 吸附边距（像素）
 * @param {number} [config.animationDuration] - 动画持续时间（毫秒）
 * @param {string} [config.easingType] - 缓动函数类型
 */
Live2DManager.prototype.setSnapConfig = function (config) {
    if (!config) return;

    if (typeof config.threshold === 'number' && config.threshold >= 0) {
        SNAP_CONFIG.threshold = config.threshold;
    }
    if (typeof config.margin === 'number' && config.margin >= 0) {
        SNAP_CONFIG.margin = config.margin;
    }
    if (typeof config.animationDuration === 'number' && config.animationDuration > 0) {
        SNAP_CONFIG.animationDuration = config.animationDuration;
    }
    if (typeof config.easingType === 'string' && EasingFunctions[config.easingType]) {
        SNAP_CONFIG.easingType = config.easingType;
    }

    console.debug('[Live2D] 吸附配置已更新:', SNAP_CONFIG);
};

/**
 * 获取当前吸附配置
 * @returns {Object} 当前配置
 */
Live2DManager.prototype.getSnapConfig = function () {
    return { ...SNAP_CONFIG };
};

/**
 * 清理所有全局事件监听器
 * 在 Live2DManager 销毁或页面卸载时调用此方法，防止内存泄漏
 */
Live2DManager.prototype.cleanupEventListeners = function () {
    console.debug('[Live2D] 开始清理全局事件监听器...');

    // 清理拖拽相关的监听器
    if (this._dragEndListener) {
        window.removeEventListener('pointerup', this._dragEndListener);
        window.removeEventListener('pointercancel', this._dragEndListener);
        this._dragEndListener = null;
    }
    if (this._dragMoveListener) {
        window.removeEventListener('pointermove', this._dragMoveListener);
        this._dragMoveListener = null;
    }

    // 清理鼠标跟踪监听器
    if (this._mouseTrackingListener) {
        window.removeEventListener('pointermove', this._mouseTrackingListener);
        this._mouseTrackingListener = null;
    }

    // 清理键盘事件监听器
    if (this._ctrlKeyDownListener) {
        window.removeEventListener('keydown', this._ctrlKeyDownListener);
        this._ctrlKeyDownListener = null;
    }
    if (this._ctrlKeyUpListener) {
        window.removeEventListener('keyup', this._ctrlKeyUpListener);
        this._ctrlKeyUpListener = null;
    }

    // 清理窗口失去焦点监听器
    if (this._windowBlurListener) {
        window.removeEventListener('blur', this._windowBlurListener);
        this._windowBlurListener = null;
    }

    // 清理锁定悬停淡化监听器
    if (this._lockedHoverFadeChangedListener) {
        window.removeEventListener('neko-locked-hover-fade-changed', this._lockedHoverFadeChangedListener);
        this._lockedHoverFadeChangedListener = null;
    }

    // 清理静止淡化定时器
    if (this._clearStationaryFadeTimer) {
        this._clearStationaryFadeTimer();
        this._clearStationaryFadeTimer = null;
    }

    // resize 吸附监听器已移除（setupResizeSnapDetection 不再存在）

    // 清理 canvas 上的滚轮和触摸监听器
    if (this.pixi_app && this.pixi_app.view) {
        const view = this.pixi_app.view;
        if (view.lastWheelListener) {
            view.removeEventListener('wheel', view.lastWheelListener);
            view.lastWheelListener = null;
        }
        if (view.lastTouchStartListener) {
            view.removeEventListener('touchstart', view.lastTouchStartListener);
            view.lastTouchStartListener = null;
        }
        if (view.lastTouchMoveListener) {
            view.removeEventListener('touchmove', view.lastTouchMoveListener);
            view.lastTouchMoveListener = null;
        }
        if (view.lastTouchEndListener) {
            view.removeEventListener('touchend', view.lastTouchEndListener);
            view.lastTouchEndListener = null;
        }
    }

    // 清理隐藏按钮定时器
    if (this._hideButtonsTimer) {
        clearTimeout(this._hideButtonsTimer);
        this._hideButtonsTimer = null;
    }

    // 清理防抖动保存定时器
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
        this._savePositionDebounceTimer = null;
    }

    // 清理缩放后吸附检测定时器
    if (this._snapCheckTimer) {
        clearTimeout(this._snapCheckTimer);
        this._snapCheckTimer = null;
    }

    // 清理点击效果恢复定时器和 ID
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }
    this._currentClickEffectId = null;

    // 清理页面卸载监听器（如果存在）
    if (this._unloadListener) {
        window.removeEventListener('beforeunload', this._unloadListener);
        this._unloadListener = null;
    }

    console.debug('[Live2D] 全局事件监听器清理完成');
};

/**
 * 设置页面卸载时的自动清理
 * 在初始化 Live2DManager 后调用此方法，确保页面关闭时清理资源
 */
Live2DManager.prototype.setupUnloadCleanup = function () {
    // 避免重复绑定
    if (this._unloadListener) {
        window.removeEventListener('beforeunload', this._unloadListener);
    }

    this._unloadListener = () => {
        this.cleanupEventListeners();
    };

    window.addEventListener('beforeunload', this._unloadListener);

    console.debug('[Live2D] 已设置页面卸载时的自动清理');
};

/**
 * 销毁 Live2DManager 实例
 * 清理所有资源，包括事件监听器、模型、PIXI 应用等
 */
Live2DManager.prototype.destroy = function () {
    console.log('[Live2D] 正在销毁 Live2DManager 实例...');

    // 首先清理所有事件监听器与自适应帧率守护
    this.cleanupEventListeners();
    this._stopIdleFpsGovernor();

    // 销毁当前模型
    if (this.currentModel) {
        if (this.currentModel.destroy) {
            this.currentModel.destroy();
        }
        this.currentModel = null;
    }

    // 销毁 PIXI 应用
    if (this.pixi_app) {
        this.pixi_app.destroy(true, { children: true, texture: true, baseTexture: true });
        this.pixi_app = null;
    }

    console.log('[Live2D] Live2DManager 实例已销毁');
};



/**
 * 播放教程模式的随机动作
 * @returns {Promise<boolean>} 是否成功播放动作
 */
Live2DManager.prototype.playTutorialMotion = async function() {
    if (!this.currentModel || !this.currentModel.motion) {
        return false;
    }

    const fileRefMotions = this.fileReferences && this.fileReferences.Motions;
    let motionGroups = [];

    if (fileRefMotions && typeof fileRefMotions === 'object') {
        motionGroups = Object.keys(fileRefMotions)
            .filter(group => group !== 'PreviewAll' && Array.isArray(fileRefMotions[group]) && fileRefMotions[group].length > 0);
    }

    if (motionGroups.length === 0 &&
        this.currentModel.internalModel &&
        this.currentModel.internalModel.motionManager &&
        this.currentModel.internalModel.motionManager.definitions) {
        const defs = this.currentModel.internalModel.motionManager.definitions;
        motionGroups = Object.keys(defs)
            .filter(group => group !== 'PreviewAll' && Array.isArray(defs[group]) && defs[group].length > 0);
    }

    if (motionGroups.length === 0) {
        return false;
    }

    const group = this.getRandomElement(motionGroups);
    if (!group) return false;

    const groupList =
        (fileRefMotions && fileRefMotions[group]) ||
        (this.currentModel.internalModel &&
            this.currentModel.internalModel.motionManager &&
            this.currentModel.internalModel.motionManager.definitions &&
            this.currentModel.internalModel.motionManager.definitions[group]) ||
        [];

    if (!Array.isArray(groupList) || groupList.length === 0) {
        return false;
    }

    const index = Math.floor(Math.random() * groupList.length);

    try {
        const motion = await this.currentModel.motion(group, index, window.live2dManager.CLICK_MOTION_PRIORITY);
        // const motion = await this.currentModel.motion(group, index, 2);
        if (motion) {
            console.log(`[Interaction] 教程模式 - 播放动作: ${group}[${index}]（优先级: ${window.live2dManager.CLICK_MOTION_PRIORITY}）`);
            // console.log(`[Interaction] 教程模式 - 播放动作: ${group}[${index}]（优先级: ${2}）`);
            return true;
        }
    } catch (error) {
        console.warn('[Interaction] 教程模式 - 动作播放失败:', error);
    }

    return false;
};

/**
 * 触发随机表情和动作（用于教程模式和点击空白区域）
 */
Live2DManager.prototype.triggerRandomEmotion = async function() {
    // 清除之前的点击效果恢复定时器
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }
    this._clickEffectRestoreToken = (this._clickEffectRestoreToken || 0) + 1;
    const restoreToken = this._clickEffectRestoreToken;
    if (typeof this._cancelSmoothReset === 'function') {
        this._cancelSmoothReset();
    }

    // 教程模式：直接随机播放表情
    if (window.isInTutorial) {
        console.log('[Interaction] 教程模式 - 随机播放表情（低优先级，将自动恢复）');
        try {
            // 获取表情列表
            let expressionNames = [];
            if (this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
                expressionNames = this.fileReferences.Expressions.map(e => e.Name).filter(Boolean);
            }

            // 随机播放表情
            if (expressionNames.length > 0) {
                const randomExpression = expressionNames[Math.floor(Math.random() * expressionNames.length)];
                console.log(`[Interaction] 教程模式 - 播放表情: ${randomExpression}（将在 ${window.live2dManager.CLICK_EFFECT_DURATION}ms 后恢复）`);
                await this.currentModel.expression(randomExpression);

                const playedMotion = await this.playTutorialMotion();

                if (!playedMotion) {
                    // 动作不可用时，回退到参数动画模拟效果
                    const model = this.currentModel.internalModel;
                    if (model && model.coreModel) {
                        // 随机晃动头部
                        const angleXIndex = model.coreModel.getParameterIndex('ParamAngleX');
                        const angleYIndex = model.coreModel.getParameterIndex('ParamAngleY');
                        const bodyAngleXIndex = model.coreModel.getParameterIndex('ParamBodyAngleX');

                        const duration = 1000 + Math.random() * 1000; // 1-2秒
                        const startTime = Date.now();

                        const setParamByIndex = (index, value) => {
                            if (index < 0) return;
                            if (typeof model.coreModel.setParameterValueByIndex === 'function') {
                                model.coreModel.setParameterValueByIndex(index, value);
                            } else {
                                model.coreModel.setParameterValueById(index, value);
                            }
                        };

                        const animate = () => {
                            const elapsed = Date.now() - startTime;
                            const progress = Math.min(elapsed / duration, 1);
                            const t = progress * Math.PI * 2; // 一个完整周期

                            setParamByIndex(angleXIndex, Math.sin(t) * 15); // -15 到 15 度
                            setParamByIndex(angleYIndex, Math.cos(t) * 10); // -10 到 10 度
                            setParamByIndex(bodyAngleXIndex, Math.sin(t * 0.5) * 5); // 更慢的身体晃动

                            if (progress < 1) {
                                requestAnimationFrame(animate);
                            } else {
                                // 动画结束，恢复默认值
                                setParamByIndex(angleXIndex, 0);
                                setParamByIndex(angleYIndex, 0);
                                setParamByIndex(bodyAngleXIndex, 0);
                            }
                        };

                        animate();
                        console.log('[Interaction] 教程模式 - 播放参数动画');
                    }
                }
            }
        } catch (error) {
            console.warn('[Interaction] 教程模式播放表情失败:', error);
        }
    } else {
        // 正常模式：使用情感系统
        // 获取可用的情感列表
        let availableEmotions = [];

        // 从 emotionMapping 中获取可用情感
        if (this.emotionMapping && this.emotionMapping.expressions) {
            availableEmotions = Object.keys(this.emotionMapping.expressions).filter(e => e !== '常驻');
        }

        // 如果没有配置情感，使用 _playTemporaryClickEffect 内部的兜底逻辑
        // 传一个占位 emotion，兜底会从 fileReferences 中随机选取
        if (availableEmotions.length === 0) {
            availableEmotions = ['_random_fallback'];
        }

        // 随机选择一个情感
        const randomEmotion = availableEmotions[Math.floor(Math.random() * availableEmotions.length)];
        console.log(`[Interaction] 点击触发随机情感: ${randomEmotion}（低优先级，将自动恢复）`);

        // 触发临时情感效果
        let didPlayEffect = false;
        try {
            // 播放低优先级的表情和动作
            didPlayEffect = await this._playTemporaryClickEffect(randomEmotion, 2, window.live2dManager.CLICK_EFFECT_DURATION);
        } catch (error) {
            console.warn('[Interaction] 触发情感失败:', error);
        }
        if (!didPlayEffect) {
            console.log('[Interaction] 没有可播放的点击效果，保持当前待机动作');
            return;
        }
        return;
    }

    // 设置恢复定时器：在效果持续时间后清除表情，恢复到常驻/默认状态
    // 使用唯一 ID 标记此次点击效果，用于判断是否应该恢复
    this._clickEffectIdSeq = (this._clickEffectIdSeq || 0) + 1;
    const clickEffectId = this._clickEffectIdSeq;
    this._currentClickEffectId = clickEffectId;
    
    this._clickEffectRestoreTimer = setTimeout(() => {
        this._clickEffectRestoreTimer = null;
        
        // 检查是否仍然是此次点击效果（没有被新的情感/点击覆盖）
        if (this._currentClickEffectId !== clickEffectId) {
            console.log('[Interaction] 点击效果已被新的情感覆盖，跳过恢复');
            return;
        }
        
        console.log('[Interaction] 点击效果持续时间结束，平滑恢复到默认状态');
        this._restoreClickEffectState({ restoreIdle: true, clickEffectId, restoreToken }).catch(e => {
            console.warn('[Interaction] 恢复点击效果状态失败:', e);
        });
    }, window.live2dManager.CLICK_EFFECT_DURATION);
};

Live2DManager.prototype._touchSetConfigHasAnimation = function(config) {
    return !!(config
        && ((Array.isArray(config.motions) && config.motions.length > 0)
            || (Array.isArray(config.expressions) && config.expressions.length > 0)));
};

Live2DManager.prototype._getCurrentTouchSetConfig = function() {
    const touchSet = this.touchSet;
    if (!touchSet || typeof touchSet !== 'object') return null;

    const modelName = this.modelName;
    if (modelName && touchSet[modelName] && typeof touchSet[modelName] === 'object') {
        return touchSet[modelName];
    }

    const looksLikeSingleModelConfig = this._touchSetConfigHasAnimation(touchSet.default)
        || Object.values(touchSet).some(entry => entry
            && typeof entry === 'object'
            && (entry.customArea || this._touchSetConfigHasAnimation(entry)));

    return looksLikeSingleModelConfig ? touchSet : null;
};

Live2DManager.prototype._getModelBoundsRect = function(model) {
    if (!model || typeof model.getBounds !== 'function') return null;

    let bounds = null;
    try {
        bounds = model.getBounds();
    } catch (_) {
        return null;
    }
    if (!bounds) return null;

    const firstFiniteNumber = (...values) => {
        for (const value of values) {
            const n = Number(value);
            if (Number.isFinite(n)) return n;
        }
        return null;
    };

    let width = firstFiniteNumber(bounds.width);
    let height = firstFiniteNumber(bounds.height);
    let left = firstFiniteNumber(bounds.left, bounds.x, bounds.minX);
    let top = firstFiniteNumber(bounds.top, bounds.y, bounds.minY);
    let right = firstFiniteNumber(
        bounds.right,
        bounds.maxX,
        left !== null && width !== null ? left + width : null
    );
    let bottom = firstFiniteNumber(
        bounds.bottom,
        bounds.maxY,
        top !== null && height !== null ? top + height : null
    );

    if ((width === null || width <= 0) && left !== null && right !== null) width = right - left;
    if ((height === null || height <= 0) && top !== null && bottom !== null) height = bottom - top;
    if (left === null && right !== null && width !== null) left = right - width;
    if (top === null && bottom !== null && height !== null) top = bottom - height;
    if (right === null && left !== null && width !== null) right = left + width;
    if (bottom === null && top !== null && height !== null) bottom = top + height;

    if (![left, top, right, bottom, width, height].every(Number.isFinite)) return null;
    if (width <= 0 || height <= 0) return null;

    return { left, top, right, bottom, width, height };
};

Live2DManager.prototype._getPreferredTouchSetHitArea = function(hitAreas, customAreaId) {
    if (customAreaId) return customAreaId;

    const areaList = Array.isArray(hitAreas)
        ? hitAreas.filter(Boolean)
        : (hitAreas ? [hitAreas] : []);
    const touchSet = this._getCurrentTouchSetConfig();

    if (touchSet) {
        const configuredArea = areaList.find(hitAreaId => this._touchSetConfigHasAnimation(touchSet[hitAreaId]));
        if (configuredArea) return configuredArea;
    }

    return areaList[0] || 'default';
};

Live2DManager.prototype._getCustomTouchAreaCreatedAt = function(area, fallbackId, fallbackIndex = 0) {
    const explicitCreatedAt = Number(area && area.createdAt);
    if (Number.isFinite(explicitCreatedAt) && explicitCreatedAt > 0) return explicitCreatedAt;

    const id = String((area && area.id) || fallbackId || '').trim();
    const match = id.match(/^custom_([0-9a-z]+)_/i);
    if (match) {
        const parsed = parseInt(match[1], 36);
        if (Number.isFinite(parsed) && parsed > 0) return parsed;
    }

    return Number.MAX_SAFE_INTEGER + fallbackIndex;
};

Live2DManager.prototype._getSortedCustomTouchAreaEntries = function(touchSet) {
    return Object.entries(touchSet || {})
        .map(([id, config], index) => ({
            id,
            config,
            index,
            area: config && config.customArea
        }))
        .filter(entry => entry.area && entry.area.rect)
        .sort((a, b) => {
            const orderA = this._getCustomTouchAreaCreatedAt(a.area, a.id, a.index);
            const orderB = this._getCustomTouchAreaCreatedAt(b.area, b.id, b.index);
            if (orderA !== orderB) return orderA - orderB;
            return a.index - b.index;
        });
};

Live2DManager.prototype._normalizeCustomTouchAreaRect = function(rect) {
    if (!rect || typeof rect !== 'object') return null;
    const x = Math.max(0, Math.min(1, Number(rect.x)));
    const y = Math.max(0, Math.min(1, Number(rect.y)));
    const width = Math.max(0, Math.min(Number(rect.width), 1 - x));
    const height = Math.max(0, Math.min(Number(rect.height), 1 - y));
    if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
    return { x, y, width, height };
};

Live2DManager.prototype._getRectIntersection = function(a, b) {
    if (!a || !b) return null;
    const left = Math.max(a.x, b.x);
    const top = Math.max(a.y, b.y);
    const right = Math.min(a.x + a.width, b.x + b.width);
    const bottom = Math.min(a.y + a.height, b.y + b.height);
    if (right <= left || bottom <= top) return null;
    return { x: left, y: top, width: right - left, height: bottom - top };
};

Live2DManager.prototype._subtractCustomTouchRect = function(rect, cutter, minSize = 0.0001) {
    const intersection = this._getRectIntersection(rect, cutter);
    if (!intersection) return [rect];

    const rectRight = rect.x + rect.width;
    const rectBottom = rect.y + rect.height;
    const cutRight = intersection.x + intersection.width;
    const cutBottom = intersection.y + intersection.height;
    const pieces = [];

    if (intersection.y - rect.y > minSize) {
        pieces.push({ x: rect.x, y: rect.y, width: rect.width, height: intersection.y - rect.y });
    }
    if (rectBottom - cutBottom > minSize) {
        pieces.push({ x: rect.x, y: cutBottom, width: rect.width, height: rectBottom - cutBottom });
    }
    if (intersection.x - rect.x > minSize) {
        pieces.push({ x: rect.x, y: intersection.y, width: intersection.x - rect.x, height: intersection.height });
    }
    if (rectRight - cutRight > minSize) {
        pieces.push({ x: cutRight, y: intersection.y, width: rectRight - cutRight, height: intersection.height });
    }

    return pieces.filter(piece => piece.width > minSize && piece.height > minSize);
};

Live2DManager.prototype._subtractCustomTouchRects = function(rects, cutters, minSize = 0.0001) {
    return cutters.reduce((remainingRects, cutter) => {
        return remainingRects.flatMap(rect => this._subtractCustomTouchRect(rect, cutter, minSize));
    }, rects).filter(rect => rect.width > minSize && rect.height > minSize);
};

Live2DManager.prototype._isPointInCustomTouchRect = function(point, rect) {
    return !!(point && rect
        && point.x >= rect.x && point.x <= rect.x + rect.width
        && point.y >= rect.y && point.y <= rect.y + rect.height);
};

Live2DManager.prototype._canTriggerTouchSetArea = function(hitAreaId) {
    const key = hitAreaId || 'default';
    this.touchSetFilter = this.touchSetFilter || {};
    const now = Date.now();
    const pointerSeq = this._lastTouchPointer && this._lastTouchPointer.seq;
    if (pointerSeq && this._lastTouchSetTriggerSeq === pointerSeq) {
        return false;
    }
    if (this._lastTouchSetTriggerAt && now - this._lastTouchSetTriggerAt < 900) {
        return false;
    }
    if (!this.touchSetFilter[key]) {
        this.touchSetFilter[key] = now;
        this._lastTouchSetTriggerAt = now;
        this._lastTouchSetTriggerKey = key;
        this._lastTouchSetTriggerSeq = pointerSeq || null;
        return true;
    }
    if (now - this.touchSetFilter[key] > 900) {
        this.touchSetFilter[key] = now;
        this._lastTouchSetTriggerAt = now;
        this._lastTouchSetTriggerKey = key;
        this._lastTouchSetTriggerSeq = pointerSeq || null;
        return true;
    }
    return false;
};

Live2DManager.prototype._playTouchSetWithFallback = async function(hitAreaId) {
    const touchSet = this._getCurrentTouchSetConfig();
    const requestedHitArea = hitAreaId || 'default';
    if (!touchSet) {
        console.log('[TouchSet] touchSet 未配置，播放随机动画');
        logLive2DClickTriggerSummary('TouchSet', {
            requestedHitArea,
            resolvedHitArea: null,
            fallback: 'random_emotion',
            reason: 'touch_set_not_configured',
            summaryType: 'routing_decision'
        });
        await this.triggerRandomEmotion();
        return false;
    }

    const useBlock = requestedHitArea;
    if (this._touchSetConfigHasAnimation(touchSet[useBlock])) {
        await this._playTouchSetAnimation(useBlock, { requestedHitArea });
        return true;
    }

    if (useBlock !== 'default' && this._touchSetConfigHasAnimation(touchSet.default)) {
        await this._playTouchSetAnimation('default', {
            requestedHitArea,
            fallback: 'default'
        });
        return true;
    }

    logLive2DClickTriggerSummary('TouchSet', {
        requestedHitArea,
        resolvedHitArea: useBlock,
        fallback: 'random_emotion',
        reason: 'touch_area_has_no_animation',
        summaryType: 'routing_decision'
    });
    await this.triggerRandomEmotion();
    return false;
};

Live2DManager.prototype._getCustomTouchAreaIdAtPoint = function(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || !this.currentModel) return null;
    const touchSet = this._getCurrentTouchSetConfig();
    if (!touchSet) return null;

    const bounds = this._getModelBoundsRect(this.currentModel);
    if (!bounds) return null;

    const customAreaEntries = typeof this._getSortedCustomTouchAreaEntries === 'function'
        ? this._getSortedCustomTouchAreaEntries(touchSet)
        : Object.entries(touchSet).map(([id, config], index) => ({ id, config, index, area: config && config.customArea }));

    const normalizedPoint = {
        x: (x - bounds.left) / bounds.width,
        y: (y - bounds.top) / bounds.height
    };
    const previousRects = [];

    for (const entry of customAreaEntries) {
        const rect = this._normalizeCustomTouchAreaRect(entry.area && entry.area.rect);
        if (!rect) continue;

        const effectiveRects = this._subtractCustomTouchRects([rect], previousRects, 0.0001);
        if (effectiveRects.some(piece => this._isPointInCustomTouchRect(normalizedPoint, piece))) {
            return entry.id;
        }
        previousRects.push(rect);
    }

    return null;
};

/**
 * 设置 触摸/点击 交互
 * 使用 pixi-live2d-display 的 'hit' 事件来检测 HitArea 点击
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 */
Live2DManager.prototype.setupHitAreaInteraction = function(model) {
    if (!model) {
        console.error('[HitArea] 模型不存在，无法设置 HitArea 交互');
        return;
    }

    if (this._touchSetHitHandler && this._touchSetHitModel) {
        try {
            if (typeof this._touchSetHitModel.off === 'function') {
                this._touchSetHitModel.off('hit', this._touchSetHitHandler);
            } else if (typeof this._touchSetHitModel.removeListener === 'function') {
                this._touchSetHitModel.removeListener('hit', this._touchSetHitHandler);
            }
        } catch (_) {}
        this._touchSetHitHandler = null;
        this._touchSetHitModel = null;
    }
    if (typeof model.removeAllListeners === 'function') {
        try { model.removeAllListeners('hit'); } catch (_) {}
    }

    // 监听模型的 hit 事件
    function dd(hitAreas) {
        // 只在非教程模式下处理 hit 事件
        // 教程模式下，通过 setupDragAndDrop 的点击检测处理
        if (window.isInTutorial) {
            return;
        }

        const manager = window.live2dManager;
        const pointerSeq = manager._lastTouchPointer && manager._lastTouchPointer.seq;
        manager._lastTouchHitAreas = Array.isArray(hitAreas)
            ? hitAreas.filter(Boolean)
            : (hitAreas ? [hitAreas] : []);
        manager._lastTouchHitSeq = pointerSeq || null;
        manager.touchSetHitEventLock = false;
        console.log('[HitArea] 记录命中的区域:', manager._lastTouchHitAreas);
    }

    this._touchSetHitHandler = dd;
    this._touchSetHitModel = model;
    model.on('hit', dd);
    
    console.log(`[HitArea] HitArea 交互已设置 : ${window.live2dManager.modelName}`);
};

/**
 * 根据 touchSet 配置播放 HitArea 对应的动画
 * @param {string} hitAreaId - HitArea ID
 */
Live2DManager.prototype._playTouchSetAnimation = async function(hitAreaId, options = {}) {
    const triggerLog = {
        requestedHitArea: options.requestedHitArea || hitAreaId || 'default',
        resolvedHitArea: hitAreaId || 'default',
        fallback: options.fallback || null,
        motionCandidates: 0,
        expressionCandidates: 0,
        motions: [],
        expressions: [],
        failedMotions: [],
        failedExpressions: []
    };

    if (this._isHandlingTouchInteraction) {
        console.log('[TouchSet] 动作正在加载中，忽略频繁连击防止状态污染');
        triggerLog.reason = 'busy';
        logLive2DClickTriggerSummary('TouchSet', triggerLog);
        return false;
    }
    this._isHandlingTouchInteraction = true;

    try {
        if (hitAreaId == null || !this.currentModel) {
            triggerLog.reason = !this.currentModel ? 'model_not_loaded' : 'missing_hit_area';
            return false;
        }
        let faceHoldingTime = window.live2dManager.CLICK_EFFECT_DURATION;
        let AnimHoldingTime = null;
        const touchSet = this._getCurrentTouchSetConfig();

        if (!touchSet || !touchSet[hitAreaId]) {
            console.log(`[TouchSet] 没有找到 ${hitAreaId} 的配置`);
            triggerLog.reason = 'touch_area_config_not_found';
            return false;
        }

        const config = touchSet[hitAreaId];
        const { motions = [], expressions = [] } = config;
        triggerLog.motionCandidates = motions.length;
        triggerLog.expressionCandidates = expressions.length;

        console.log(`[TouchSet] 播放 ${hitAreaId} 的动画:`, { motions, expressions });

        if (motions.length > 0) {
            const randomMotion = motions[Math.floor(Math.random() * motions.length)];

            const motionDefs = this.currentModel.internalModel?.motionManager?.definitions;
            const fileRefs = this.fileReferences?.Motions;

            const motionSources = [
                motionDefs,
                fileRefs
            ].filter(Boolean);

            let foundMotion = null;
            let foundGroupName = null;
            const normalizeMotionFileName = (file) => {
                const normalized = String(file || '').replace(/\\/g, '/');
                const relativePath = normalized.replace(/^(?:\.\/)?motions\//i, '');
                return relativePath.replace(/\.motion3\.json$/i, '').replace(/\.motion3$/i, '').replace(/\.json$/i, '');
            };

            outerLoop:
            for (const motionSource of motionSources) {
                for (const [groupName, motionList] of Object.entries(motionSource)) {
                    if (Array.isArray(motionList)) {
                        const motion = motionList.find(m => {
                            if (!m || !m.File) return false;
                            return normalizeMotionFileName(m.File) === normalizeMotionFileName(randomMotion);
                        });
                        if (motion) {
                            foundMotion = motion;
                            foundGroupName = groupName;
                            break outerLoop;
                        }
                    }
                }
            }

            if (!foundMotion) {
                triggerLog.failedMotions.push({ name: randomMotion, reason: 'motion_not_found' });
                console.warn(`[TouchSet] 找不到匹配的动作: ${randomMotion}`);
            } else {
                const { motion } = { motion: foundMotion };
                const groupName = foundGroupName;
                console.log(`[TouchSet] 准备播放动作: ${groupName}, 文件: ${motion.File}`);

                try {
                    let motionPath = motion.File;
                    if (!motionPath.startsWith('http') && !motionPath.startsWith('/')) {
                        motionPath = `${this.modelRootPath}/${motionPath}`;
                    }
                    const response = await fetch(motionPath);
                    if (response.ok) {
                        const motionData = await response.json();
                        if (motionData.Meta && motionData.Meta.Duration) {
                            AnimHoldingTime = motionData.Meta.Duration * 1000;
                            faceHoldingTime = AnimHoldingTime;
                            console.log(`[TouchSet] 动作持续时间: ${AnimHoldingTime}ms, 表情持续时间将同步`);
                        }
                    }
                } catch (error) {
                    console.warn(`[TouchSet] 无法获取motion持续时间:`, error);
                }

                let backupDefs, backupGroups, backupSettingsMotions, backupJsonMotions, backupJsonFileRefs;
                let groupExisted = false;
                let internalModel, motionManager, json, live2dModel;

                try {
                    internalModel = this.currentModel.internalModel;
                    motionManager = internalModel.motionManager;
                    json = internalModel.settings.json;

                    backupDefs = motionManager.definitions?.[groupName];
                        backupGroups = motionManager.motionGroups?.[groupName];
                        backupSettingsMotions = internalModel.settings.motions?.[groupName];
                        backupJsonMotions = json?.motions?.[groupName];
                        backupJsonFileRefs = json?.FileReferences?.Motions?.[groupName];

                        groupExisted = backupDefs !== undefined || backupGroups !== undefined;

                        let tempMotionsList = [{ 'File': motion.File }];

                        if (json) {
                            if (!json.FileReferences) json.FileReferences = {};
                            if (!json.FileReferences.Motions) json.FileReferences.Motions = {};
                            json.FileReferences.Motions[groupName] = tempMotionsList;
                            if (!json.motions) json.motions = {};
                            json.motions[groupName] = tempMotionsList;
                        }

                        if (!internalModel.settings.motions) internalModel.settings.motions = {};
                        internalModel.settings.motions[groupName] = tempMotionsList;

                        if (!motionManager.definitions) motionManager.definitions = {};
                        motionManager.definitions[groupName] = tempMotionsList;

                        if (!motionManager.motionGroups) motionManager.motionGroups = {};
                        motionManager.motionGroups[groupName] = [];

                        live2dModel = this.currentModel;
                        console.log(`[TouchSet] 正在向引擎注入并加载动作: ${motion.File}`);
                        await motionManager.loadMotion(groupName, 0);

                        if (live2dModel !== this.currentModel) {
                            console.log('[TouchSet] 模型已切换，中止动作播放');
                            triggerLog.reason = 'model_changed_during_motion';
                            return false;
                        }

                        motionManager.stopAllMotions();
                        const result = await live2dModel.motion(groupName, 0, 3);

                        if (result) {
                            triggerLog.motions.push({
                                name: randomMotion,
                                group: groupName,
                                index: 0,
                                file: motion.File,
                                durationMs: AnimHoldingTime,
                                priority: 3
                            });
                            console.log(`[TouchSet] ✅ 成功下发播放指令: ${groupName}[0]`);
                        } else {
                            triggerLog.failedMotions.push({
                                name: randomMotion,
                                group: groupName,
                                index: 0,
                                file: motion.File,
                                reason: 'motion_returned_falsy'
                            });
                            console.warn(`[TouchSet] ❌ 动作加载成功但引擎仍拒绝播放: ${groupName}[0]`);
                        }
                    } catch (error) {
                        triggerLog.failedMotions.push({
                            name: randomMotion,
                            group: groupName,
                            index: 0,
                            file: motion.File,
                            reason: error?.message || String(error)
                        });
                        console.warn(`[TouchSet] 动作播放异常: ${groupName}[0]`, error);
                    } finally {
                        if (groupExisted) {
                            if (backupDefs !== undefined) motionManager.definitions[groupName] = backupDefs;
                            if (backupGroups !== undefined) motionManager.motionGroups[groupName] = backupGroups;
                            if (backupSettingsMotions !== undefined) internalModel.settings.motions[groupName] = backupSettingsMotions;
                            if (backupJsonMotions !== undefined) {
                                if (json) json.motions[groupName] = backupJsonMotions;
                            }
                            if (backupJsonFileRefs !== undefined) {
                                if (json?.FileReferences?.Motions) json.FileReferences.Motions[groupName] = backupJsonFileRefs;
                            }
                        } else {
                            delete motionManager.definitions?.[groupName];
                            delete motionManager.motionGroups?.[groupName];
                            delete internalModel.settings.motions?.[groupName];
                            if (json) {
                                delete json.motions?.[groupName];
                                delete json.FileReferences?.Motions?.[groupName];
                            }
                        }
                    }
            }
        }

        if (triggerLog.motions.length === 0 && expressions.length > 0) {
            const randomExpressionName = expressions[Math.floor(Math.random() * expressions.length)];
            const faceInfo = this.fileReferences?.Expressions?.find(e => e.Name === randomExpressionName);
            if (!faceInfo || !faceInfo.File) {
                triggerLog.failedExpressions.push({ name: randomExpressionName, reason: 'expression_file_not_found' });
                console.warn(`[TouchSet] 表情文件不存在: ${randomExpressionName}`);
            } else {
                console.log(`[TouchSet] 尝试播放表情: ${faceInfo.File}`);
                try {
                    const expressionResult = await this.playExpression(randomExpressionName, faceInfo.File);
                    if (expressionResult !== false) {
                        triggerLog.expressions.push({
                            name: randomExpressionName,
                            file: faceInfo.File,
                            durationMs: faceHoldingTime,
                            fallbackFor: 'motion'
                        });
                        console.log(`[TouchSet] 播放表情成功: ${randomExpressionName}, 持续时间: ${faceHoldingTime}ms`);
                    } else {
                        triggerLog.failedExpressions.push({
                            name: randomExpressionName,
                            file: faceInfo.File,
                            reason: 'play_returned_false'
                        });
                        console.warn(`[TouchSet] 表情播放返回失败: ${randomExpressionName}`);
                    }

                    clearTimeout(this.expressionTimer);
                    const holdingTime = Number.isFinite(faceHoldingTime) && faceHoldingTime > 0 ? faceHoldingTime : 3000;
                    this.expressionTimer = setTimeout(() => {
                        if (typeof this.clearExpression === 'function') {
                            this.clearExpression();
                            console.log(`[TouchSet] 临时表情清除，准备恢复常驻状态`);
                            if (typeof this.applyPersistentExpressionsNative === 'function') {
                                try {
                                    this.applyPersistentExpressionsNative(true);
                                } catch (_) {}
                            }
                        }
                    }, holdingTime);
                } catch (e) {
                    triggerLog.failedExpressions.push({
                        name: randomExpressionName,
                        file: faceInfo.File,
                        reason: e?.message || String(e)
                    });
                    console.warn(`[TouchSet] 播放表情失败: ${randomExpressionName}`, e);
                }
            }
        }
        if (triggerLog.motions.length === 0 && triggerLog.expressions.length === 0) {
            triggerLog.reason = triggerLog.reason || 'nothing_played';
        }
        return triggerLog.motions.length + triggerLog.expressions.length > 0;
    } catch (error) {
        triggerLog.reason = triggerLog.reason || 'exception';
        console.warn(`[TouchSet] 播放动画失败:`, error);
        return false;
    } finally {
        logLive2DClickTriggerSummary('TouchSet', triggerLog);
        this._isHandlingTouchInteraction = false;
    }
};
