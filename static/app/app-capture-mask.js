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
    // Default re-encode quality when the caller does not pass ctx.jpegQuality
    // (the proactive screenshot stream capture uses 0.85).
    var JPEG_REENCODE_QUALITY = 0.85;
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
    function collectLocalMaskRects(doc, win, options) {
        var rects = [];
        if (!doc || typeof doc.querySelectorAll !== 'function' || !win) return rects;
        // A hidden tab is not on screen. The desktop-shell reporter passes
        // includeHiddenDocument: the shell keeps the last report of a hidden
        // window and uses it again as soon as the window is shown.
        if (doc.visibilityState === 'hidden' && !(options && options.includeHiddenDocument)) return rects;
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
            // Cheap rejection before the style walks below (long histories).
            if (box.left >= viewportW || box.top >= viewportH
                || box.left + box.width <= 0 || box.top + box.height <= 0) continue;
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
     *   'viewport' — the capture is this tab's viewport (exact);
     *   'screen'   — the capture is the whole (single) display. Best effort:
     *                the browser chrome offset is estimated from outer/inner
     *                window sizes, so page zoom other than 100% or unusual
     *                window frames can shift the result by a few pixels; the
     *                MASK_PADDING_CSS_PX margin absorbs small errors only;
     *   anything else — position unknown.
     * Normalized output makes the capture resolution / devicePixelRatio
     * irrelevant: the painter multiplies by the real image size.
     *
     * @returns {{known: boolean, rects: Array}} rects in 0..1 when known
     */
    // Left + right window border at 100% page zoom is a few px (0 when
    // maximized, ~16 on Windows); the smallest Chromium zoom step (90% / 110%)
    // changes innerWidth by far more than this on any usable window width
    // (best effort: a very narrow window at 110% can stay under it).
    var MAX_SIDE_CHROME_CSS_PX = 40;

    function mapViewportRectsToCapture(rects, captureType, geom) {
        if (!rects || rects.length === 0) return { known: true, rects: [] };
        if (!geom) return { known: false, rects: [] };
        var originX = 0, originY = 0, refW, refH;
        if (captureType === 'viewport') {
            refW = Number(geom.innerWidth);
            refH = Number(geom.innerHeight);
        } else if (captureType === 'screen') {
            // The page cannot tell which display of several was captured, and
            // without a display count it cannot tell whether there are several.
            if (geom.multiDisplay !== false) return { known: false, rects: [] };
            var outerW = Number(geom.outerWidth) || 0;
            var outerH = Number(geom.outerHeight) || 0;
            var innerW = Number(geom.innerWidth) || 0;
            var innerH = Number(geom.innerHeight) || 0;
            // Page zoom other than 100% scales innerWidth / DOM rects (CSS px)
            // but not outerWidth / screen size, so the two cannot be mixed. It
            // shows as inner > outer (zoomed out) or a side gap far wider than
            // a window border (zoomed in; also a docked side panel). Either
            // way the position is unknown: when unsure, mask less.
            var sideChrome = outerW - innerW;
            if (sideChrome < 0 || sideChrome > MAX_SIDE_CHROME_CSS_PX || outerH - innerH < 0) {
                return { known: false, rects: [] };
            }
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
        var mapped = rects.map(function (r) {
            return {
                x: (originX + r.x) / refW,
                y: (originY + r.y) / refH,
                width: r.width / refW,
                height: r.height / refH
            };
        });
        return { known: true, rects: clipNormalizedRects(mapped) };
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
        // true: several displays; false: known single display; null: unknown
        // (no Screen.isExtended, e.g. Firefox / Safari). Unknown counts as
        // "position unknown" for whole-screen captures: when unsure, mask less.
        var multiDisplay = null;
        if (typeof scr.isExtended === 'boolean') {
            multiDisplay = scr.isExtended;
        } else if (win.appScreen && typeof win.appScreen.isKnownMultiDisplay === 'function') {
            try { if (win.appScreen.isKnownMultiDisplay()) multiDisplay = true; } catch (e) { multiDisplay = null; }
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

    // ======================== User switch ========================

    // Settings switch captureSelfUiMaskEnabled (default on). Read on every
    // frame, never cached: another window (e.g. the chat window in desktop
    // multi-window mode) can change it, and app-settings.js applies that
    // change here through the shared settings snapshot. Only an explicit
    // false turns masking off.
    function isMaskEnabled(win) {
        try {
            var S = win.appState;
            var value = S && typeof S.captureSelfUiMaskEnabled === 'boolean'
                ? S.captureSelfUiMaskEnabled
                : win.captureSelfUiMaskEnabled;
            return value !== false;
        } catch (e) { return true; }
    }

    // ======================== getDisplayMedia hand-out order (desktop shell) ========================

    // For a stream without a recorded source the shell query names no source,
    // and the shell answers with the source its display-media handler last
    // handed this window. That record follows the latest request, not the
    // stream: once a newer getDisplayMedia request was made here, an older
    // stream still in use would be masked at the newer source's position.
    // Every request is numbered (before the call: even a cancelled or failed
    // one may change the shell's record), its stream remembers the number, and
    // frames of a superseded stream are position unknown without asking.
    var displayMediaRequestSeq = 0;
    var displayMediaStreamSeqs = typeof WeakMap === 'function' ? new WeakMap() : null;

    /**
     * getDisplayMedia, numbered. Call it in place of
     * mediaDevices.getDisplayMedia(): it calls that synchronously (user
     * activation is kept) and resolves to the same stream.
     */
    function requestDisplayMedia(mediaDevices, constraints) {
        var seq = ++displayMediaRequestSeq;
        var request = mediaDevices.getDisplayMedia(constraints);
        return Promise.resolve(request).then(function (stream) {
            if (stream && displayMediaStreamSeqs) displayMediaStreamSeqs.set(stream, seq);
            return stream;
        });
    }

    // A query that would name no source for a display-media stream that is
    // no longer this window's latest request.
    function isSupersededDisplayMediaStream(ctx) {
        if (!ctx || ctx.sourceId || ctx.via === 'backend' || !ctx.stream || !displayMediaStreamSeqs) return false;
        var seq = displayMediaStreamSeqs.get(ctx.stream);
        return typeof seq === 'number' && seq !== displayMediaRequestSeq;
    }

    // ======================== Geometry resolution ========================

    // Same captured surface as far as the shell query is concerned: the C2
    // request only carries sourceId and surface, so captureType (decided from
    // the frame after capture) must not be part of the comparison.
    function sameMaskTarget(a, b) {
        if (!a || !b) return false;
        return (a.sourceId || null) === (b.sourceId || null)
            && surfaceForContext(a) === surfaceForContext(b);
    }

    /**
     * Snapshot the regions before a frame is captured. It is combined with a
     * second snapshot taken after capture (see intersectGeometry): only what
     * both agree on is masked.
     *
     * @param {{sourceId?: string|null, via?: string, captureType?: string|null}} [ctxHint]
     *        best guess of what is about to be captured
     */
    function beginMaskSession(ctxHint) {
        var win = window;
        var hint = ctxHint || {};
        // Switched off: no sample, no shell query. The session still records
        // that, so a frame whose capture started while off is sent as
        // captured even if the switch is turned on before it is masked (no
        // pre-capture sample to intersect with: when unsure, mask less).
        if (!isMaskEnabled(win)) return { hint: hint, switchedOff: true };
        if (isDesktopMultiWindow(win)) {
            if (isSupersededDisplayMediaStream(hint)) {
                return { hint: hint, pending: Promise.resolve({ known: false, rects: [], reason: 'superseded_stream' }) };
            }
            return { hint: hint, pending: queryShellMask(win, hint) };
        }
        // Keep the window geometry of this sample too: if the browser window
        // moves before the post-capture sample, each sample is mapped with
        // the position it was taken at.
        return { hint: hint, local: sampleLocal(win), geom: readBrowserGeometry(win) };
    }

    // One sample of this page's regions. Regions of a background tab are
    // collected too: whether they can be in the frame depends on what is
    // captured (see mapLocalSample).
    function sampleLocal(win) {
        var doc = win.document;
        var focused = false;
        try { focused = !!doc && typeof doc.hasFocus === 'function' && doc.hasFocus() === true; } catch (e) { focused = false; }
        return {
            rects: collectLocalMaskRects(doc, win, { includeHiddenDocument: true }),
            hidden: !!doc && doc.visibilityState === 'hidden',
            // Only a focused browser window is known to be in front. Without
            // focus, another application may cover the chat regions, and a
            // mask painted at their screen position would land on it.
            focused: focused
        };
    }

    function mapLocalSample(sample, ctx, geom) {
        var captureType = ctx && ctx.captureType;
        if (captureType === 'viewport') {
            var tabKind = classifyTabCapture(ctx && ctx.stream);
            // Another N.E.K.O page is being shared: its chat UI is in the
            // frame, but at positions only that page knows.
            if (tabKind === 'other-neko') return { known: false, rects: [] };
            // Another tab is being shared: none of this page is in the frame.
            if (tabKind === 'other') return { known: true, rects: [] };
            // This tab is being shared: it keeps producing frames in the
            // background, so its regions are masked even while hidden.
            if (tabKind === 'own') return mapViewportRectsToCapture(sample.rects, captureType, geom);
            // Unknown (no Capture Handle, e.g. Firefox / Safari / insecure
            // origin): the shared tab may not be this page, so the position
            // of anything to mask is unknown. When unsure, mask less.
            if (!sample.rects || sample.rects.length === 0) return { known: true, rects: [] };
            return { known: false, rects: [] };
        }
        // A background tab is not on any screen or window capture.
        if (sample.hidden) return { known: true, rects: [] };
        // Whole-screen capture of a browser window that is not in front: the
        // regions may be covered by another application, so what the frame
        // shows there is unknown. When unsure, mask less. (A tab capture
        // above gets the page itself, covered or not.)
        if (captureType === 'screen' && !sample.focused && sample.rects && sample.rects.length > 0) {
            return { known: false, rects: [] };
        }
        return mapViewportRectsToCapture(sample.rects, captureType, geom);
    }

    // ======================== Own-tab detection (Capture Handle) ========================
    // A browser-tab share only reports displaySurface 'browser'; it does not
    // say which tab. This page publishes a random capture handle, and a tab
    // share whose track reports that handle is this page.
    var OWN_CAPTURE_HANDLE_PREFIX = 'neko-self-ui-mask:';
    var OWN_CAPTURE_HANDLE = OWN_CAPTURE_HANDLE_PREFIX + Math.random().toString(36).slice(2) + Date.now().toString(36);
    var ownCaptureHandleConfigured = false;

    function configureOwnCaptureHandle(win) {
        try {
            var md = win.navigator && win.navigator.mediaDevices;
            if (!md || typeof md.setCaptureHandleConfig !== 'function') return;
            var origin = win.location && win.location.origin;
            md.setCaptureHandleConfig({
                handle: OWN_CAPTURE_HANDLE,
                exposeOrigin: false,
                permittedOrigins: origin ? [origin] : []
            });
            ownCaptureHandleConfigured = true;
        } catch (e) {
            // Not a top-level secure context, or unsupported: stay unknown.
            ownCaptureHandleConfigured = false;
        }
    }

    /**
     * What a browser-tab share captures.
     * @returns {'own'|'other-neko'|'other'|null} 'own': this tab; 'other-neko':
     *          another N.E.K.O page (its chat UI is in the frame, at positions
     *          this page cannot know); 'other': some other tab; null: cannot tell
     *          (no Capture Handle support, or no stream passed).
     */
    function classifyTabCapture(stream) {
        if (!ownCaptureHandleConfigured) return null;
        // Only the stream the frame came from can answer; the cached stream
        // may have been swapped while the frame was being captured.
        if (!stream) return null;
        var track = null;
        try {
            var tracks = typeof stream.getVideoTracks === 'function' ? stream.getVideoTracks() : [];
            track = tracks && tracks[0];
        } catch (e) { track = null; }
        if (!track || typeof track.getCaptureHandle !== 'function') return null;
        var captureHandle = null;
        try { captureHandle = track.getCaptureHandle(); } catch (e) { return null; }
        // Our handle is set and readable by our own origin, so any other
        // answer (another handle, or none) is a different tab.
        // Coupling: setCaptureHandleConfig is per document and the last call
        // wins. Two other scripts in this repo call it with their own handle:
        //   - static/game/sdk/neko-minigame-vision-host.js (sets and clears it)
        //   - static/libs/trtc.js (sets it on load, permittedOrigins ['*'];
        //     only loaded by visit_transport.html today)
        // Neither is loaded on the pages that load this module (index.html,
        // chat.html). If one ever is, own-tab shares would read as "another
        // tab" here and stop being masked. The browser offers no way to read
        // back the current config, so this cannot be detected at runtime.
        var handle = captureHandle && captureHandle.handle;
        if (handle === OWN_CAPTURE_HANDLE) return 'own';
        // Only same-origin N.E.K.O pages are recognised: the handle is
        // published to its own origin only, so a N.E.K.O page on another
        // origin (localhost vs 127.0.0.1, another port) reads as 'other'.
        if (typeof handle === 'string' && handle.indexOf(OWN_CAPTURE_HANDLE_PREFIX) === 0) return 'other-neko';
        return 'other';
    }

    /**
     * @returns {boolean|null} true: the stream captures this tab; false: it
     *          captures another tab, including another N.E.K.O page (use
     *          classifyTabCapture() to tell those apart); null: cannot tell.
     */
    function isOwnTabCapture(stream) {
        var kind = classifyTabCapture(stream);
        return kind === null ? null : kind === 'own';
    }

    async function resolveGeometry(session, ctx) {
        var win = window;
        if (isDesktopMultiWindow(win)) {
            if (isSupersededDisplayMediaStream(ctx)) return { known: false, rects: [], reason: 'superseded_stream' };
            var post = await queryShellMask(win, ctx);
            var pre = null;
            // A snapshot taken for a different capture target describes a
            // different surface; only the post-capture query applies then.
            if (session && session.pending && sameMaskTarget(session.hint, ctx)) {
                pre = await session.pending;
            }
            return intersectGeometry(pre, post);
        }
        var geom = readBrowserGeometry(win);
        var postLocal = mapLocalSample(sampleLocal(win), ctx, geom);
        if (!(session && session.local)) return postLocal;
        // Each sample is mapped with the window position it was taken at.
        var preLocal = mapLocalSample(session.local, ctx, session.geom || geom);
        return intersectGeometry(preLocal, postLocal);
    }

    /**
     * Combine the samples taken before and after capture. The frame was taken
     * somewhere in between, so only what both samples agree on is certain:
     * mask the overlap of the two, nothing more (when unsure, mask less).
     * A region that moved, appeared or disappeared during capture is left
     * unmasked in that frame. Either side unknown -> unknown. Both sides
     * known with no overlap -> known, nothing to mask: such a frame does not
     * go through decideWhenGeometryUnavailable(), so a future "drop" policy
     * would not drop it (the position of each sample is known; they just
     * disagree). A missing side
     * (no pre-capture sample, or one taken for a different target) leaves the
     * other side as is.
     */
    function intersectGeometry(pre, post) {
        if (!pre) return post || { known: false, rects: [] };
        if (!post) return pre;
        if (!pre.known || !post.known) return { known: false, rects: [] };
        var rects = [];
        var seen = {};
        (pre.rects || []).forEach(function (a) {
            (post.rects || []).forEach(function (b) {
                var left = Math.max(a.x, b.x);
                var top = Math.max(a.y, b.y);
                var right = Math.min(a.x + a.width, b.x + b.width);
                var bottom = Math.min(a.y + a.height, b.y + b.height);
                if (!(right > left) || !(bottom > top)) return;
                var r = { x: left, y: top, width: right - left, height: bottom - top };
                var key = [r.x, r.y, r.width, r.height].join(',');
                if (seen[key]) return;
                seen[key] = true;
                rects.push(r);
            });
        });
        return { known: true, rects: rects };
    }

    // ======================== Fallback policy (single place) ========================

    /**
     * The chat history / option regions may be in the frame, but where they
     * are is unknown: several displays in the browser, or a display count the
     * browser does not report (no Screen.isExtended); a whole-screen share
     * while the browser window is not focused (another application may cover
     * the regions; a desktop shell chat window that is not always-on-top is
     * the shell's ok:false); a shared window that may
     * or may not be this browser; a shared browser tab that cannot be tied to
     * this page (no Capture Handle) or shows another N.E.K.O page of the same
     * origin; the samples before and after capture where either side is
     * unknown; a desktop shell without getSelfUiCaptureMask, or a failed query.
     *
     * Also reached when the position is known but painting failed
     * (reason 'paint_failed': decode / 2d context / encode error).
     *
     * Current policy: send the frame unmasked in both cases, as before this
     * module existed. Return 'drop' instead to withhold such frames (for all
     * reasons, or only for one); every caller already treats a null result
     * from maskFrameForModel() as "no frame". One exception to check when
     * switching to 'drop': the backend's screenshot_request (app-websocket.js)
     * sends no reply when it gets no frame, and the backend then takes its own
     * desktop screenshot after the timeout, unmasked. That entry would have to
     * reply declined: true for a dropped frame.
     *
     * @param {object} ctx the frame context passed to maskFrameForModel()
     * @param {'position_unknown'|'paint_failed'} reason
     */
    function decideWhenGeometryUnavailable(ctx, reason) {
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
    async function paintMaskOnDataUrl(dataUrl, rects, jpegQuality) {
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
        var out = isPng ? canvas.toDataURL('image/png') : canvas.toDataURL('image/jpeg', jpegQuality);
        if (!out || out === 'data:,') throw new Error('mask: encode failed');
        return out;
    }

    // ======================== Entry point ========================

    /**
     * Mask N.E.K.O's own chat history / option regions in a screen frame
     * before it is sent to the model.
     *
     * @param {string} dataUrl the captured frame
     * @param {{captureType: ('screen'|'viewport'|null), sourceId?: string|null, via?: string, jpegQuality?: number, stream?: MediaStream}} ctx
     *        what this frame actually is (pinned when it was captured).
     *        jpegQuality: re-encode quality for JPEG frames (match the capture
     *        quality). stream: the stream the frame came from. Callers must pass
     *        it for browser-tab frames: it is the only way to tell this tab from
     *        another one. Without it the tab is "unknown" (position unknown, so
     *        the fallback policy decides: sent unmasked); the cached stream is never
     *        consulted, since it may have been swapped during capture.
     * @param {object} [session] from beginMaskSession(), taken before capture
     * @returns {Promise<string|null>} the frame to send, or null to send nothing
     */
    async function maskFrameForModel(dataUrl, ctx, session) {
        if (!dataUrl) return dataUrl;
        // Switched off: the frame goes out as captured, without reading any
        // region or asking the desktop shell.
        if (!isMaskEnabled(window)) return dataUrl;
        if (session && session.switchedOff) return dataUrl;
        var geometry;
        try {
            geometry = await resolveGeometry(session, ctx || {});
        } catch (e) {
            geometry = { known: false, rects: [] };
        }
        if (geometry.known && geometry.rects.length === 0) return dataUrl;
        var reason = 'position_unknown';
        if (geometry.known) {
            reason = 'paint_failed';
            try {
                var quality = ctx && isFiniteNumber(ctx.jpegQuality) && ctx.jpegQuality > 0 && ctx.jpegQuality <= 1
                    ? ctx.jpegQuality : JPEG_REENCODE_QUALITY;
                return await paintMaskOnDataUrl(dataUrl, geometry.rects, quality);
            } catch (e) {
                console.warn('[CaptureMask] masking failed:', e && e.message);
            }
        }
        return decideWhenGeometryUnavailable(ctx, reason) === 'drop' ? null : dataUrl;
    }

    // ======================== Chat-window reporter (contract C1) ========================

    var REPORT_THROTTLE_MS = 150;
    var REPORT_RECHECK_MS = 1000;

    // The shell rejects a report with more than this many rects (and then
    // treats the window as unknown), so larger sets are merged first.
    var REPORT_MAX_RECTS = 16;

    function unionRect(a, b) {
        var left = Math.min(a.x, b.x);
        var top = Math.min(a.y, b.y);
        var right = Math.max(a.x + a.width, b.x + b.width);
        var bottom = Math.max(a.y + a.height, b.y + b.height);
        return { x: left, y: top, width: right - left, height: bottom - top };
    }

    /**
     * Merge rects until at most `max` remain, always merging the pair whose
     * bounding box adds the least area. Merging only grows coverage, so no
     * region that was marked becomes unmasked. This is the one place that
     * masks more than the marked regions while the position is known: the
     * shell rejects more than REPORT_MAX_RECTS rects (and would then send
     * the frame unmasked), and it only kicks in past that count.
     */
    function limitRectCount(rects, max) {
        var list = (rects || []).slice();
        if (list.length <= max) return list;
        if (list.length > 128) {
            // Pathological counts: one bounding box keeps this O(n).
            return [list.reduce(unionRect)];
        }
        while (list.length > max) {
            var bestI = 0, bestJ = 1, bestCost = Infinity;
            for (var i = 0; i < list.length; i++) {
                for (var j = i + 1; j < list.length; j++) {
                    var u = unionRect(list[i], list[j]);
                    var cost = u.width * u.height
                        - list[i].width * list[i].height
                        - list[j].width * list[j].height;
                    if (cost < bestCost) { bestCost = cost; bestI = i; bestJ = j; }
                }
            }
            var merged = unionRect(list[bestI], list[bestJ]);
            list.splice(bestJ, 1);
            list[bestI] = merged;
        }
        return list;
    }

    /**
     * C1 payload. `visible` means "this page has regions to mask" and follows
     * the layout only: whether the window itself is shown is the shell's
     * call, and the shell drops the rects of a visible:false report. A hidden
     * window therefore keeps reporting its real regions with visible:true, so
     * the stored report is current the moment the window is shown again.
     * Nothing to mask -> visible:false with no rects.
     */
    function buildReportPayload(doc, win) {
        var rects = limitRectCount(collectLocalMaskRects(doc, win, { includeHiddenDocument: true }), REPORT_MAX_RECTS);
        return {
            version: CONTRACT_VERSION,
            visible: rects.length > 0,
            viewport: { width: Number(win.innerWidth) || 0, height: Number(win.innerHeight) || 0 },
            rects: rects
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
                characterData: true,
                attributeFilter: ['class', 'style', 'hidden', MASK_ATTRIBUTE]
            });
        }
        win.addEventListener('resize', schedule);
        doc.addEventListener('visibilitychange', schedule);
        doc.addEventListener('transitionend', schedule, true);
        doc.addEventListener('animationend', schedule, true);
        // Scrolling moves regions without any DOM mutation (captured: scroll
        // does not bubble from inner scroll containers).
        doc.addEventListener('scroll', schedule, true);
        // Low-frequency recheck; only sends when the payload changed.
        setInterval(schedule, REPORT_RECHECK_MS);
        schedule();
        return { flush: flush };
    }

    configureOwnCaptureHandle(window);

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
        requestDisplayMedia: requestDisplayMedia,
        maskFrameForModel: maskFrameForModel,
        decideWhenGeometryUnavailable: decideWhenGeometryUnavailable,
        buildReportPayload: buildReportPayload,
        limitRectCount: limitRectCount,
        REPORT_MAX_RECTS: REPORT_MAX_RECTS,
        startShellReporter: startShellReporter,
        isOwnTabCapture: isOwnTabCapture,
        classifyTabCapture: classifyTabCapture,
        OWN_CAPTURE_HANDLE: OWN_CAPTURE_HANDLE
    };
})();
