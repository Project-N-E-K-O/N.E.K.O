/**
 * app-capture-mask.js — hide N.E.K.O's own chat history and reply-option UI
 * from screen frames before they are handed to the model (issue #3474).
 *
 * Every "screen frame -> model" path goes through maskFrameForModel():
 *   - single-shot screenshots (proactive chat, backend screenshot_request),
 *     via captureProactiveChatScreenshotWithSource in app-proactive.js;
 *   - continuous / heartbeat screen frames (manual screen share, native
 *     screen share, voice-time vision heartbeat), via
 *     buildMaskedStreamDataMessage in app-screen.js.
 * Callers await it and send only what it returns, so a frame is never sent
 * before it is masked.
 *
 * Regions are elements marked with [data-neko-capture-mask] in the React chat
 * window (chat history list and the reply-option slot). The composer input is
 * intentionally not marked and stays visible.
 *
 * Where the regions are in the captured image:
 *   - Browser (single window): the chat lives in this document; viewport rects
 *     are mapped with the same captureType convention as the Avatar overlay.
 *   - Desktop shell (multi-window): the chat lives in other windows, so the
 *     chat windows report their rects with window.nekoCaptureMask.report()
 *     (contract C1) and the capturing window asks the shell for rects already
 *     mapped into the captured surface with
 *     window.electronScreen.getSelfUiCaptureMask() (contract C2).
 *
 * When the regions are visible but their position in the captured image is
 * unknown, decideWhenGeometryUnavailable() is the single place that decides
 * what to do with the frame.
 *
 * Privacy: never log image data or chat text from this module.
 */
(function () {
    'use strict';

    var MASK_ATTRIBUTE = 'data-neko-capture-mask';
    var MASK_PADDING_CSS_PX = 8;
    var MASK_FILL_STYLE = '#2b2b2b';
    var CONTRACT_VERSION = 1;
    var SHELL_QUERY_TIMEOUT_MS = 500;
    var JPEG_REENCODE_QUALITY = 0.9;
    var PIXEL_EPSILON = 1e-6;

    function isFiniteNumber(value) {
        return typeof value === 'number' && isFinite(value);
    }

    // ======================== Region collection (local DOM) ========================

    function isEffectivelyVisible(el, win) {
        var node = el;
        while (node && node.nodeType === 1) {
            var style = null;
            try { style = win.getComputedStyle(node); } catch (e) { style = null; }
            if (style) {
                if (style.display === 'none') return false;
                if (node === el && (style.visibility === 'hidden' || style.visibility === 'collapse')) return false;
                var opacity = parseFloat(style.opacity);
                if (isFiniteNumber(opacity) && opacity <= 0.01) return false;
            }
            node = node.parentElement;
        }
        return true;
    }

    function clipsOverflow(value) {
        return value === 'hidden' || value === 'auto' || value === 'scroll' || value === 'clip';
    }

    function clipToOverflowAncestors(el, win, rect) {
        var r = { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
        var node = el.parentElement;
        while (node && node.nodeType === 1) {
            var style = null;
            try { style = win.getComputedStyle(node); } catch (e) { style = null; }
            if (style && (clipsOverflow(style.overflowX) || clipsOverflow(style.overflowY) || clipsOverflow(style.overflow))) {
                var box;
                try { box = node.getBoundingClientRect(); } catch (e) { box = null; }
                if (box) {
                    r.left = Math.max(r.left, box.left);
                    r.top = Math.max(r.top, box.top);
                    r.right = Math.min(r.right, box.left + box.width);
                    r.bottom = Math.min(r.bottom, box.top + box.height);
                    if (r.right <= r.left || r.bottom <= r.top) return null;
                }
            }
            node = node.parentElement;
        }
        return r;
    }

    /**
     * Visible marked regions as viewport CSS-pixel rects, padded and clipped
     * to the viewport. Elements that are absent, collapsed to zero size,
     * hidden or fully transparent produce nothing.
     */
    function collectLocalMaskRects(doc, win) {
        var rects = [];
        if (!doc || typeof doc.querySelectorAll !== 'function' || !win) return rects;
        if (doc.visibilityState === 'hidden') return rects;
        var viewportW = Number(win.innerWidth) || 0;
        var viewportH = Number(win.innerHeight) || 0;
        if (viewportW <= 0 || viewportH <= 0) return rects;
        var nodes;
        try { nodes = doc.querySelectorAll('[' + MASK_ATTRIBUTE + ']'); } catch (e) { return rects; }
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            var box;
            try { box = el.getBoundingClientRect(); } catch (e) { continue; }
            if (!box || !(box.width > 0) || !(box.height > 0)) continue;
            if (!isEffectivelyVisible(el, win)) continue;
            // Content scrolled out of a clipping container (e.g. older messages
            // in the history list) is not on screen; clip before padding.
            var clip = clipToOverflowAncestors(el, win, {
                left: box.left, top: box.top, right: box.left + box.width, bottom: box.top + box.height
            });
            if (!clip) continue;
            var left = Math.max(0, clip.left - MASK_PADDING_CSS_PX);
            var top = Math.max(0, clip.top - MASK_PADDING_CSS_PX);
            var right = Math.min(viewportW, clip.right + MASK_PADDING_CSS_PX);
            var bottom = Math.min(viewportH, clip.bottom + MASK_PADDING_CSS_PX);
            if (right <= left || bottom <= top) continue;
            rects.push({ x: left, y: top, width: right - left, height: bottom - top });
        }
        return rects;
    }

    // ======================== Coordinate mapping (browser) ========================

    /**
     * Map viewport CSS rects into the captured image, normalized to 0..1.
     * Same convention as getAvatarScreenPosition in app-screen.js:
     *   'viewport' — the capture is this tab's viewport;
     *   'screen'   — the capture is the whole (single) display;
     *   anything else — position unknown.
     * Normalized output makes the capture resolution / devicePixelRatio
     * irrelevant: the painter multiplies by the real image size.
     *
     * @returns {{known: boolean, rects: Array}} rects in 0..1 when known
     */
    function mapViewportRectsToCapture(rects, captureType, geom) {
        if (!rects || rects.length === 0) return { known: true, rects: [] };
        if (!geom) return { known: false, rects: [] };
        var originX = 0, originY = 0, refW, refH;
        if (captureType === 'viewport') {
            refW = Number(geom.innerWidth);
            refH = Number(geom.innerHeight);
        } else if (captureType === 'screen') {
            // The page cannot tell which display of several was captured.
            if (geom.multiDisplay) return { known: false, rects: [] };
            var outerW = Number(geom.outerWidth) || 0;
            var outerH = Number(geom.outerHeight) || 0;
            var innerW = Number(geom.innerWidth) || 0;
            var innerH = Number(geom.innerHeight) || 0;
            var chromeLeft = Math.max(0, Math.round((outerW - innerW) / 2));
            var chromeTop = Math.max(0, outerH - innerH - chromeLeft);
            originX = (Number(geom.screenX) || 0) + chromeLeft;
            originY = (Number(geom.screenY) || 0) + chromeTop;
            refW = Number(geom.screenWidth);
            refH = Number(geom.screenHeight);
        } else {
            return { known: false, rects: [] };
        }
        if (!(refW > 0) || !(refH > 0)) return { known: false, rects: [] };
        return {
            known: true,
            rects: clipNormalizedRects(rects.map(function (r) {
                return {
                    x: (originX + r.x) / refW,
                    y: (originY + r.y) / refH,
                    width: r.width / refW,
                    height: r.height / refH
                };
            }))
        };
    }

    function clipNormalizedRects(rects) {
        var out = [];
        for (var i = 0; i < (rects || []).length; i++) {
            var r = rects[i];
            if (!r) continue;
            var x = Number(r.x), y = Number(r.y), w = Number(r.width), h = Number(r.height);
            if (![x, y, w, h].every(isFiniteNumber) || !(w > 0) || !(h > 0)) continue;
            var left = Math.max(0, x);
            var top = Math.max(0, y);
            var right = Math.min(1, x + w);
            var bottom = Math.min(1, y + h);
            if (right <= left || bottom <= top) continue;
            out.push({ x: left, y: top, width: right - left, height: bottom - top });
        }
        return out;
    }

    function readBrowserGeometry(win) {
        var scr = win.screen || {};
        var multiDisplay = false;
        if (win.appScreen && typeof win.appScreen.isKnownMultiDisplay === 'function') {
            try { multiDisplay = !!win.appScreen.isKnownMultiDisplay(); } catch (e) { multiDisplay = false; }
        } else if (typeof scr.isExtended === 'boolean') {
            multiDisplay = scr.isExtended;
        }
        return {
            innerWidth: win.innerWidth,
            innerHeight: win.innerHeight,
            outerWidth: win.outerWidth,
            outerHeight: win.outerHeight,
            screenX: win.screenX,
            screenY: win.screenY,
            screenWidth: scr.width,
            screenHeight: scr.height,
            multiDisplay: multiDisplay
        };
    }

    // ======================== Desktop shell (contract C2) ========================

    function isDesktopMultiWindow(win) {
        return win.__NEKO_MULTI_WINDOW__ === true;
    }

    function surfaceForContext(ctx) {
        // 'backend': the backend's own desktop screenshot; the shell knows
        // which display(s) that covers on this platform.
        return ctx && ctx.via === 'backend' ? 'backend' : 'source';
    }

    function withTimeout(promise, ms) {
        return new Promise(function (resolve) {
            var done = false;
            var timer = setTimeout(function () {
                if (done) return;
                done = true;
                resolve(null);
            }, ms);
            Promise.resolve(promise).then(function (value) {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve(value);
            }, function () {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve(null);
            });
        });
    }

    async function queryShellMask(win, ctx) {
        var bridge = win.electronScreen;
        if (!bridge || typeof bridge.getSelfUiCaptureMask !== 'function') {
            return { known: false, rects: [], reason: 'shell_api_missing' };
        }
        var request = {
            version: CONTRACT_VERSION,
            sourceId: (ctx && typeof ctx.sourceId === 'string' && ctx.sourceId) ? ctx.sourceId : null,
            surface: surfaceForContext(ctx)
        };
        var res;
        try {
            res = await withTimeout(bridge.getSelfUiCaptureMask(request), SHELL_QUERY_TIMEOUT_MS);
        } catch (e) {
            res = null;
        }
        if (!res || res.ok !== true || !Array.isArray(res.rects)) {
            return { known: false, rects: [], reason: 'shell_unavailable' };
        }
        return { known: true, rects: clipNormalizedRects(res.rects) };
    }

    // ======================== Geometry resolution ========================

    function sameMaskTarget(a, b) {
        if (!a || !b) return false;
        return (a.sourceId || null) === (b.sourceId || null)
            && surfaceForContext(a) === surfaceForContext(b)
            && (a.captureType || null) === (b.captureType || null);
    }

    /**
     * Snapshot the regions before a frame is captured. The result is unioned
     * with a second snapshot taken after capture, so a chat window that moves
     * while the frame is being taken is still covered.
     *
     * @param {{sourceId?: string|null, via?: string, captureType?: string|null}} [ctxHint]
     *        best guess of what is about to be captured
     */
    function beginMaskSession(ctxHint) {
        var win = window;
        var hint = ctxHint || {};
        if (isDesktopMultiWindow(win)) {
            return { hint: hint, pending: queryShellMask(win, hint) };
        }
        return { hint: hint, localRects: collectLocalMaskRects(win.document, win) };
    }

    async function resolveGeometry(session, ctx) {
        var win = window;
        if (isDesktopMultiWindow(win)) {
            var post = await queryShellMask(win, ctx);
            var pre = null;
            // A snapshot taken for a different capture target describes a
            // different surface; only the post-capture query applies then.
            if (session && session.pending && sameMaskTarget(session.hint, ctx)) {
                pre = await session.pending;
            }
            return unionGeometry(pre, post);
        }
        var geom = readBrowserGeometry(win);
        var postLocal = mapViewportRectsToCapture(collectLocalMaskRects(win.document, win), ctx && ctx.captureType, geom);
        var preLocal = session && session.localRects
            ? mapViewportRectsToCapture(session.localRects, ctx && ctx.captureType, geom)
            : null;
        return unionGeometry(preLocal, postLocal);
    }

    function unionGeometry(pre, post) {
        var known = false;
        var rects = [];
        var seen = {};
        [pre, post].forEach(function (g) {
            if (!g || !g.known) return;
            known = true;
            (g.rects || []).forEach(function (r) {
                var key = [r.x, r.y, r.width, r.height].join(',');
                if (seen[key]) return;
                seen[key] = true;
                rects.push(r);
            });
        });
        return { known: known, rects: rects };
    }

    // ======================== Fallback policy (single place) ========================

    /**
     * The chat history / option regions may be in the frame, but where they
     * are is unknown (several displays in the browser, a shared window that
     * may or may not be this browser, a desktop shell without
     * getSelfUiCaptureMask, or a failed query).
     *
     * Current policy: send the frame unmasked, as before this module existed.
     * Return 'drop' instead to withhold such frames; every caller already
     * treats a null result from maskFrameForModel() as "no frame".
     */
    function decideWhenGeometryUnavailable(ctx) {
        return 'send-unmasked';
    }

    // ======================== Painting ========================

    function paintMaskRects(ctx2d, width, height, rects) {
        ctx2d.fillStyle = MASK_FILL_STYLE;
        for (var i = 0; i < rects.length; i++) {
            var r = rects[i];
            // Round outwards (cover partial pixels), ignoring float noise.
            var left = Math.max(0, Math.floor(r.x * width + PIXEL_EPSILON));
            var top = Math.max(0, Math.floor(r.y * height + PIXEL_EPSILON));
            var right = Math.min(width, Math.ceil((r.x + r.width) * width - PIXEL_EPSILON));
            var bottom = Math.min(height, Math.ceil((r.y + r.height) * height - PIXEL_EPSILON));
            if (right > left && bottom > top) {
                ctx2d.fillRect(left, top, right - left, bottom - top);
            }
        }
    }

    function loadImage(dataUrl) {
        return new Promise(function (resolve, reject) {
            var img = new Image();
            img.onload = function () { resolve(img); };
            img.onerror = function () { reject(new Error('mask: image decode failed')); };
            img.src = dataUrl;
        });
    }

    /**
     * Paint the normalized rects onto a copy of the image. The image keeps
     * its pixel size and encoding (JPEG stays JPEG, PNG stays PNG).
     */
    async function paintMaskOnDataUrl(dataUrl, rects) {
        var img = await loadImage(dataUrl);
        var width = img.naturalWidth || img.width;
        var height = img.naturalHeight || img.height;
        if (!(width > 0) || !(height > 0)) throw new Error('mask: empty image');
        var canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        var ctx2d = canvas.getContext('2d');
        if (!ctx2d) throw new Error('mask: no 2d context');
        ctx2d.drawImage(img, 0, 0, width, height);
        paintMaskRects(ctx2d, width, height, rects);
        var isPng = /^data:image\/png[;,]/i.test(dataUrl);
        var out = isPng ? canvas.toDataURL('image/png') : canvas.toDataURL('image/jpeg', JPEG_REENCODE_QUALITY);
        if (!out || out === 'data:,') throw new Error('mask: encode failed');
        return out;
    }

    // ======================== Entry point ========================

    /**
     * Mask N.E.K.O's own chat history / option regions in a screen frame
     * before it is sent to the model.
     *
     * @param {string} dataUrl the captured frame
     * @param {{captureType: ('screen'|'viewport'|null), sourceId?: string|null, via?: string}} ctx
     *        what this frame actually is (pinned when it was captured)
     * @param {object} [session] from beginMaskSession(), taken before capture
     * @returns {Promise<string|null>} the frame to send, or null to send nothing
     */
    async function maskFrameForModel(dataUrl, ctx, session) {
        if (!dataUrl) return dataUrl;
        var geometry;
        try {
            geometry = await resolveGeometry(session, ctx || {});
        } catch (e) {
            geometry = { known: false, rects: [] };
        }
        if (geometry.known && geometry.rects.length === 0) return dataUrl;
        if (geometry.known) {
            try {
                return await paintMaskOnDataUrl(dataUrl, geometry.rects);
            } catch (e) {
                console.warn('[CaptureMask] masking failed:', e && e.message);
            }
        }
        return decideWhenGeometryUnavailable(ctx) === 'drop' ? null : dataUrl;
    }

    // ======================== Chat-window reporter (contract C1) ========================

    var REPORT_THROTTLE_MS = 80;
    var REPORT_RECHECK_MS = 1000;

    function buildReportPayload(doc, win) {
        var hidden = doc.visibilityState === 'hidden';
        return {
            version: CONTRACT_VERSION,
            visible: !hidden,
            viewport: { width: Number(win.innerWidth) || 0, height: Number(win.innerHeight) || 0 },
            rects: hidden ? [] : collectLocalMaskRects(doc, win)
        };
    }

    function startShellReporter(win, doc) {
        var reporter = win.nekoCaptureMask;
        if (!reporter || typeof reporter.report !== 'function') return null;
        var lastJson = null;
        var timer = null;

        function flush() {
            timer = null;
            var payload = buildReportPayload(doc, win);
            var json = JSON.stringify(payload);
            if (json === lastJson) return;
            lastJson = json;
            try {
                var result = reporter.report(payload);
                if (result && typeof result.catch === 'function') result.catch(function () { lastJson = null; });
            } catch (e) {
                lastJson = null;
            }
        }

        function schedule() {
            if (timer) return;
            timer = setTimeout(flush, REPORT_THROTTLE_MS);
        }

        if (typeof win.MutationObserver === 'function' && doc.documentElement) {
            var observer = new win.MutationObserver(schedule);
            observer.observe(doc.documentElement, {
                subtree: true,
                childList: true,
                attributes: true,
                attributeFilter: ['class', 'style', 'hidden', MASK_ATTRIBUTE]
            });
        }
        win.addEventListener('resize', schedule);
        doc.addEventListener('visibilitychange', schedule);
        doc.addEventListener('transitionend', schedule, true);
        doc.addEventListener('animationend', schedule, true);
        // Low-frequency recheck; only sends when the payload changed.
        setInterval(schedule, REPORT_RECHECK_MS);
        schedule();
        return { flush: flush };
    }

    // Only chat windows of the desktop shell expose nekoCaptureMask.report.
    try {
        if (window.nekoCaptureMask && typeof window.nekoCaptureMask.report === 'function') {
            startShellReporter(window, document);
        }
    } catch (e) {
        console.warn('[CaptureMask] reporter init failed:', e && e.message);
    }

    window.appCaptureMask = {
        MASK_ATTRIBUTE: MASK_ATTRIBUTE,
        MASK_PADDING_CSS_PX: MASK_PADDING_CSS_PX,
        MASK_FILL_STYLE: MASK_FILL_STYLE,
        collectLocalMaskRects: collectLocalMaskRects,
        mapViewportRectsToCapture: mapViewportRectsToCapture,
        clipNormalizedRects: clipNormalizedRects,
        paintMaskRects: paintMaskRects,
        beginMaskSession: beginMaskSession,
        maskFrameForModel: maskFrameForModel,
        decideWhenGeometryUnavailable: decideWhenGeometryUnavailable,
        buildReportPayload: buildReportPayload,
        startShellReporter: startShellReporter
    };
})();
