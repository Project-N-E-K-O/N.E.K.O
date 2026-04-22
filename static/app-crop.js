/**
 * app-crop.js — Full-screen screenshot crop tool
 *
 * Flow: capture full screen → show overlay → user selects region →
 *       confirm (✓) saves to clipboard + returns dataUrl, cancel (×) clears selection,
 *       top-bar "取消" or right-click exits entirely.
 *
 * Supports: drag to create selection, move selection, resize via corner/edge handles.
 *
 * Exports: window.appCrop
 *   - cropImage(dataUrl, opts) → Promise<string|null>
 *     opts.recaptureFn: async () => dataUrl  (called by "隐藏NEKO" tab)
 */
(function () {
    'use strict';

    var mod = {};

    // ======================== State ========================
    var overlay = null;
    var canvas = null;
    var ctx = null;
    var imgEl = null;
    var resolvePromise = null;
    var sourceDataUrl = null;
    var recaptureFn = null;
    // 单调递增 token：防止旧 recaptureFn 异步返回时把结果灌进新一轮 crop 会话，
    // 或在 finally 里把新会话刚显示的"隐藏NEKO"按钮文案/disabled 状态错误重置。
    var recaptureRunId = 0;

    // Selection rectangle (canvas coords, always normalized: x,y = top-left)
    var sel = null; // { x, y, w, h } or null

    // Interaction mode
    var MODE_NONE = 0;
    var MODE_NEW = 1;      // drawing new selection
    var MODE_MOVE = 2;     // moving existing selection
    var MODE_RESIZE = 3;   // resizing via handle
    var mode = MODE_NONE;

    // Drag bookkeeping
    var dragStartX = 0, dragStartY = 0;
    var dragOrigSel = null; // snapshot of sel at drag start
    var resizeHandle = '';  // 'nw','n','ne','e','se','s','sw','w'

    // Image display metrics
    var imgDisplayLeft = 0, imgDisplayTop = 0;
    var imgDisplayWidth = 0, imgDisplayHeight = 0;
    var imgNaturalWidth = 0, imgNaturalHeight = 0;

    // DOM refs
    var topBar = null;
    var actionBtns = null; // the ✓ / × floating div
    var tabScreenshot = null;
    var tabHideNeko = null;
    var activeTab = 'screenshot'; // 'screenshot' | 'hideNeko'

    var HANDLE_SIZE = 8;
    var MIN_SEL = 10;

    // ======================== i18n helpers ========================
    function tr(key, fallback) {
        try {
            if (typeof window.t === 'function') {
                var v = window.t(key);
                if (v && v !== key) return v;
            }
        } catch (e) { /* fall through */ }
        return fallback;
    }

    // ======================== Ensure DOM ========================
    function ensureOverlay() {
        if (overlay) return;

        overlay = document.createElement('div');
        overlay.id = 'crop-overlay';
        overlay.className = 'crop-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.style.display = 'none';

        // Background image
        imgEl = document.createElement('img');
        imgEl.className = 'crop-bg-image';
        imgEl.draggable = false;
        overlay.appendChild(imgEl);

        // Canvas
        canvas = document.createElement('canvas');
        canvas.className = 'crop-canvas';
        overlay.appendChild(canvas);
        ctx = canvas.getContext('2d');

        // ---- Top bar ----
        topBar = document.createElement('div');
        topBar.className = 'crop-topbar';

        tabScreenshot = document.createElement('button');
        tabScreenshot.className = 'crop-tab crop-tab-active';
        tabScreenshot.type = 'button';
        tabScreenshot.textContent = tr('chat.cropTabScreenshot', '\u622A\u56FE');
        tabScreenshot.addEventListener('click', function () { switchTab('screenshot'); });

        tabHideNeko = document.createElement('button');
        tabHideNeko.className = 'crop-tab';
        tabHideNeko.type = 'button';
        tabHideNeko.textContent = tr('chat.cropTabHideNeko', '\u9690\u85CFNEKO');
        tabHideNeko.addEventListener('click', function () { switchTab('hideNeko'); });

        var tabCancel = document.createElement('button');
        tabCancel.className = 'crop-tab crop-tab-cancel';
        tabCancel.type = 'button';
        tabCancel.textContent = tr('chat.cropTabCancel', '\u53D6\u6D88');
        tabCancel.addEventListener('click', cancelAll);

        topBar.appendChild(tabScreenshot);
        topBar.appendChild(tabHideNeko);
        topBar.appendChild(tabCancel);
        overlay.appendChild(topBar);

        // ---- Floating action buttons (✓ / ×) ----
        actionBtns = document.createElement('div');
        actionBtns.className = 'crop-action-btns';
        actionBtns.style.display = 'none';

        var btnConfirm = document.createElement('button');
        btnConfirm.className = 'crop-action-btn crop-action-confirm';
        btnConfirm.type = 'button';
        btnConfirm.innerHTML = '&#x2713;';
        btnConfirm.title = tr('chat.cropConfirmTitle', '\u786E\u8BA4\u622A\u56FE');
        btnConfirm.addEventListener('click', confirmCrop);

        var btnCancel = document.createElement('button');
        btnCancel.className = 'crop-action-btn crop-action-cancel';
        btnCancel.type = 'button';
        btnCancel.innerHTML = '&#x2717;';
        btnCancel.title = tr('chat.cropClearSelectionTitle', '\u53D6\u6D88\u9009\u533A');
        btnCancel.addEventListener('click', clearSelection);

        actionBtns.appendChild(btnCancel);
        actionBtns.appendChild(btnConfirm);
        overlay.appendChild(actionBtns);

        // ---- Events ----
        canvas.addEventListener('mousedown', onPointerDown);
        document.addEventListener('mousemove', onPointerMove);
        document.addEventListener('mouseup', onPointerUp);
        canvas.addEventListener('touchstart', onTouchStart, { passive: false });
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onTouchEnd);

        // Right-click to cancel entirely
        canvas.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            cancelAll();
        });

        overlay.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') cancelAll();
        });

        document.body.appendChild(overlay);
    }

    // ======================== Tab switching ========================
    function switchTab(tab) {
        if (tab === activeTab) return;
        activeTab = tab;
        tabScreenshot.classList.toggle('crop-tab-active', tab === 'screenshot');
        tabHideNeko.classList.toggle('crop-tab-active', tab === 'hideNeko');
        clearSelection();

        if (tab === 'hideNeko' && recaptureFn) {
            var runId = ++recaptureRunId;
            var currentRecaptureFn = recaptureFn;
            tabHideNeko.disabled = true;
            tabHideNeko.textContent = tr('chat.cropTabRecapturing', '\u6B63\u5728\u91CD\u65B0\u622A\u56FE...');
            currentRecaptureFn().then(function (newDataUrl) {
                if (runId !== recaptureRunId) return;
                if (newDataUrl && activeTab === 'hideNeko') {
                    sourceDataUrl = newDataUrl;
                    loadImage(newDataUrl);
                }
            }).catch(function (err) {
                if (runId !== recaptureRunId) return;
                console.warn('[crop] recapture failed:', err);
            }).finally(function () {
                if (runId !== recaptureRunId) return;
                tabHideNeko.disabled = false;
                tabHideNeko.textContent = tr('chat.cropTabHideNeko', '\u9690\u85CFNEKO');
            });
        } else if (tab === 'hideNeko' && !recaptureFn) {
            console.warn('[crop] 点了 hideNeko 但 recaptureFn 未设置，无法重截图');
        }
    }

    // ======================== Coordinate helpers ========================
    function getTopBarHeight() {
        return (topBar && topBar.offsetHeight) || 40;
    }

    function computeImgMetrics() {
        var overlayW = overlay.clientWidth;
        var overlayH = overlay.clientHeight - getTopBarHeight();
        var natW = imgEl.naturalWidth;
        var natH = imgEl.naturalHeight;
        imgNaturalWidth = natW;
        imgNaturalHeight = natH;

        var scale = Math.min(overlayW / natW, overlayH / natH, 1);
        imgDisplayWidth = Math.round(natW * scale);
        imgDisplayHeight = Math.round(natH * scale);
        imgDisplayLeft = Math.round((overlayW - imgDisplayWidth) / 2);
        imgDisplayTop = Math.round((overlayH - imgDisplayHeight) / 2);
    }

    function canvasToImage(cx, cy) {
        var ix = (cx - imgDisplayLeft) / imgDisplayWidth * imgNaturalWidth;
        var iy = (cy - imgDisplayTop) / imgDisplayHeight * imgNaturalHeight;
        return { x: ix, y: iy };
    }

    function getPointerPos(e) {
        var rect = canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    function clampSel(s) {
        if (!s) return null;
        var x = s.x, y = s.y, w = s.w, h = s.h;
        var right = imgDisplayLeft + imgDisplayWidth;
        var bottom = imgDisplayTop + imgDisplayHeight;
        if (x < imgDisplayLeft) { w -= (imgDisplayLeft - x); x = imgDisplayLeft; }
        if (y < imgDisplayTop) { h -= (imgDisplayTop - y); y = imgDisplayTop; }
        if (x + w > right) w = right - x;
        if (y + h > bottom) h = bottom - y;
        if (w < 1 || h < 1) return null;
        return { x: x, y: y, w: w, h: h };
    }

    // ======================== Hit testing ========================
    function hitTestHandle(px, py) {
        if (!sel) return '';
        var hs = HANDLE_SIZE + 4; // generous hit area
        var cx = sel.x, cy = sel.y, cw = sel.w, ch = sel.h;
        var mx = cx + cw / 2, my = cy + ch / 2;

        // Corners
        if (Math.abs(px - cx) <= hs && Math.abs(py - cy) <= hs) return 'nw';
        if (Math.abs(px - (cx + cw)) <= hs && Math.abs(py - cy) <= hs) return 'ne';
        if (Math.abs(px - cx) <= hs && Math.abs(py - (cy + ch)) <= hs) return 'sw';
        if (Math.abs(px - (cx + cw)) <= hs && Math.abs(py - (cy + ch)) <= hs) return 'se';

        // Edges (midpoints)
        if (Math.abs(px - mx) <= cw / 2 && Math.abs(py - cy) <= hs) return 'n';
        if (Math.abs(px - mx) <= cw / 2 && Math.abs(py - (cy + ch)) <= hs) return 's';
        if (Math.abs(px - cx) <= hs && Math.abs(py - my) <= ch / 2) return 'w';
        if (Math.abs(px - (cx + cw)) <= hs && Math.abs(py - my) <= ch / 2) return 'e';

        return '';
    }

    function hitTestInside(px, py) {
        if (!sel) return false;
        return px >= sel.x && px <= sel.x + sel.w &&
               py >= sel.y && py <= sel.y + sel.h;
    }

    function getCursorForHandle(h) {
        var map = { nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
                    n: 'ns-resize', s: 'ns-resize', w: 'ew-resize', e: 'ew-resize' };
        return map[h] || 'crosshair';
    }

    // ======================== Drawing ========================
    function drawOverlay() {
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        // Dark mask
        ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        ctx.fillRect(0, 0, w, h);

        if (!sel) {
            // No selection — show image area more clearly
            ctx.clearRect(imgDisplayLeft, imgDisplayTop, imgDisplayWidth, imgDisplayHeight);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
            ctx.fillRect(imgDisplayLeft, imgDisplayTop, imgDisplayWidth, imgDisplayHeight);
            return;
        }

        var cs = clampSel(sel);
        if (!cs) return;

        // Clear selected region
        ctx.clearRect(cs.x, cs.y, cs.w, cs.h);

        // Border
        ctx.strokeStyle = '#44b7fe';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        ctx.strokeRect(cs.x, cs.y, cs.w, cs.h);
        ctx.setLineDash([]);

        // Corner + edge handles
        drawHandles(cs);

        // Dimension label
        var c1 = canvasToImage(cs.x, cs.y);
        var c2 = canvasToImage(cs.x + cs.w, cs.y + cs.h);
        var cropW = Math.round(Math.abs(c2.x - c1.x));
        var cropH = Math.round(Math.abs(c2.y - c1.y));
        if (cropW > 0 && cropH > 0) {
            var label = cropW + ' \u00D7 ' + cropH;
            ctx.font = '12px sans-serif';
            var m = ctx.measureText(label);
            var lx = cs.x + cs.w / 2 - m.width / 2 - 4;
            var ly = cs.y + cs.h + 20;
            if (ly > h - 30) ly = cs.y - 10;

            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            var rw = m.width + 8, rh = 20, rx = lx, ry = ly - 14, rr = 4;
            ctx.beginPath();
            if (ctx.roundRect) {
                ctx.roundRect(rx, ry, rw, rh, rr);
            } else {
                ctx.moveTo(rx + rr, ry);
                ctx.lineTo(rx + rw - rr, ry);
                ctx.arcTo(rx + rw, ry, rx + rw, ry + rr, rr);
                ctx.lineTo(rx + rw, ry + rh - rr);
                ctx.arcTo(rx + rw, ry + rh, rx + rw - rr, ry + rh, rr);
                ctx.lineTo(rx + rr, ry + rh);
                ctx.arcTo(rx, ry + rh, rx, ry + rh - rr, rr);
                ctx.lineTo(rx, ry + rr);
                ctx.arcTo(rx, ry, rx + rr, ry, rr);
                ctx.closePath();
            }
            ctx.fill();
            ctx.fillStyle = '#fff';
            ctx.fillText(label, lx + 4, ly);
        }
    }

    function drawHandles(s) {
        var hs = HANDLE_SIZE;
        ctx.fillStyle = '#44b7fe';
        var pts = [
            [s.x, s.y], [s.x + s.w, s.y],
            [s.x, s.y + s.h], [s.x + s.w, s.y + s.h],
            [s.x + s.w / 2, s.y], [s.x + s.w / 2, s.y + s.h],
            [s.x, s.y + s.h / 2], [s.x + s.w, s.y + s.h / 2]
        ];
        for (var i = 0; i < pts.length; i++) {
            ctx.fillRect(pts[i][0] - hs / 2, pts[i][1] - hs / 2, hs, hs);
        }
    }

    // ======================== Action buttons position ========================
    function positionActionBtns() {
        if (!sel || !actionBtns) { actionBtns.style.display = 'none'; return; }
        var cs = clampSel(sel);
        if (!cs) { actionBtns.style.display = 'none'; return; }

        actionBtns.style.display = 'flex';
        var canvasRect = canvas.getBoundingClientRect();
        var btnW = actionBtns.offsetWidth || 72;
        var btnH = actionBtns.offsetHeight || 32;

        var left = canvasRect.left + cs.x + cs.w - btnW;
        var top = canvasRect.top + cs.y + cs.h + 6;

        // If overflows bottom, put above selection
        if (top + btnH > window.innerHeight - 10) {
            top = canvasRect.top + cs.y - btnH - 6;
        }
        // Clamp to viewport
        if (left < 4) left = 4;
        if (left + btnW > window.innerWidth - 4) left = window.innerWidth - btnW - 4;

        actionBtns.style.left = left + 'px';
        actionBtns.style.top = top + 'px';
    }

    // ======================== Pointer events ========================
    function onPointerDown(e) {
        if (e.button === 2) return; // right-click handled by contextmenu
        e.preventDefault();
        var pos = getPointerPos(e);

        // 1. Check handle hit
        var handle = hitTestHandle(pos.x, pos.y);
        if (handle) {
            mode = MODE_RESIZE;
            resizeHandle = handle;
            dragStartX = pos.x;
            dragStartY = pos.y;
            dragOrigSel = { x: sel.x, y: sel.y, w: sel.w, h: sel.h };
            return;
        }

        // 2. Check inside hit → move
        if (hitTestInside(pos.x, pos.y)) {
            mode = MODE_MOVE;
            dragStartX = pos.x;
            dragStartY = pos.y;
            dragOrigSel = { x: sel.x, y: sel.y, w: sel.w, h: sel.h };
            return;
        }

        // 3. New selection
        mode = MODE_NEW;
        dragStartX = pos.x;
        dragStartY = pos.y;
        sel = { x: pos.x, y: pos.y, w: 0, h: 0 };
        hideActionBtns();
        drawOverlay();
    }

    function onPointerMove(e) {
        if (mode === MODE_NONE) {
            // Update cursor based on hover
            if (!canvas || !overlay || overlay.style.display === 'none') return;
            var pos = getPointerPos(e);
            var h = hitTestHandle(pos.x, pos.y);
            if (h) {
                canvas.style.cursor = getCursorForHandle(h);
            } else if (hitTestInside(pos.x, pos.y)) {
                canvas.style.cursor = 'move';
            } else {
                canvas.style.cursor = 'crosshair';
            }
            return;
        }

        e.preventDefault();
        var pos = getPointerPos(e);
        var dx = pos.x - dragStartX;
        var dy = pos.y - dragStartY;

        if (mode === MODE_NEW) {
            sel = normRect(dragStartX, dragStartY, pos.x, pos.y);
        } else if (mode === MODE_MOVE) {
            sel = {
                x: dragOrigSel.x + dx,
                y: dragOrigSel.y + dy,
                w: dragOrigSel.w,
                h: dragOrigSel.h
            };
            // Constrain to image area
            if (sel.x < imgDisplayLeft) sel.x = imgDisplayLeft;
            if (sel.y < imgDisplayTop) sel.y = imgDisplayTop;
            if (sel.x + sel.w > imgDisplayLeft + imgDisplayWidth) sel.x = imgDisplayLeft + imgDisplayWidth - sel.w;
            if (sel.y + sel.h > imgDisplayTop + imgDisplayHeight) sel.y = imgDisplayTop + imgDisplayHeight - sel.h;
        } else if (mode === MODE_RESIZE) {
            sel = resizeSel(dragOrigSel, resizeHandle, dx, dy);
        }

        drawOverlay();
    }

    function onPointerUp(e) {
        if (mode === MODE_NONE) return;
        var prevMode = mode;
        mode = MODE_NONE;

        if (prevMode === MODE_NEW) {
            var pos = getPointerPos(e);
            sel = normRect(dragStartX, dragStartY, pos.x, pos.y);
        }

        // Validate selection
        var cs = clampSel(sel);
        if (!cs || cs.w < MIN_SEL || cs.h < MIN_SEL) {
            sel = null;
            hideActionBtns();
        } else {
            sel = cs;
            positionActionBtns();
        }
        drawOverlay();
    }

    // Touch adapters
    function onTouchStart(e) {
        if (e.touches.length !== 1) return;
        e.preventDefault();
        var t = e.touches[0];
        onPointerDown({ button: 0, preventDefault: function () {}, clientX: t.clientX, clientY: t.clientY });
    }
    function onTouchMove(e) {
        if (e.touches.length !== 1) return;
        e.preventDefault();
        var t = e.touches[0];
        onPointerMove({ preventDefault: function () {}, clientX: t.clientX, clientY: t.clientY });
    }
    function onTouchEnd(e) {
        var t = e.changedTouches[0];
        onPointerUp({ clientX: t.clientX, clientY: t.clientY });
    }

    // ======================== Rect helpers ========================
    function normRect(x1, y1, x2, y2) {
        return {
            x: Math.min(x1, x2), y: Math.min(y1, y2),
            w: Math.abs(x2 - x1), h: Math.abs(y2 - y1)
        };
    }

    function resizeSel(orig, handle, dx, dy) {
        var x = orig.x, y = orig.y, w = orig.w, h = orig.h;
        if (handle.indexOf('w') !== -1) { x += dx; w -= dx; }
        if (handle.indexOf('e') !== -1) { w += dx; }
        if (handle.indexOf('n') !== -1) { y += dy; h -= dy; }
        if (handle.indexOf('s') !== -1) { h += dy; }
        // Prevent inversion
        if (w < MIN_SEL) { w = MIN_SEL; if (handle.indexOf('w') !== -1) x = orig.x + orig.w - MIN_SEL; }
        if (h < MIN_SEL) { h = MIN_SEL; if (handle.indexOf('n') !== -1) y = orig.y + orig.h - MIN_SEL; }
        return { x: x, y: y, w: w, h: h };
    }

    // ======================== Actions ========================
    function hideActionBtns() {
        if (actionBtns) actionBtns.style.display = 'none';
    }

    function clearSelection() {
        sel = null;
        mode = MODE_NONE;
        hideActionBtns();
        drawOverlay();
    }

    function cropToDataUrl() {
        var cs = clampSel(sel);
        if (!cs) return null;
        var c1 = canvasToImage(cs.x, cs.y);
        var c2 = canvasToImage(cs.x + cs.w, cs.y + cs.h);
        var cx = Math.max(0, Math.round(c1.x));
        var cy = Math.max(0, Math.round(c1.y));
        var cw = Math.min(imgNaturalWidth - cx, Math.round(c2.x - c1.x));
        var ch = Math.min(imgNaturalHeight - cy, Math.round(c2.y - c1.y));
        if (cw < 1 || ch < 1) return null;

        var tmpCanvas = document.createElement('canvas');
        tmpCanvas.width = cw;
        tmpCanvas.height = ch;
        var tmpCtx = tmpCanvas.getContext('2d');
        tmpCtx.drawImage(imgEl, cx, cy, cw, ch, 0, 0, cw, ch);
        return tmpCanvas.toDataURL('image/jpeg', 0.9);
    }

    function copyToClipboard(dataUrl) {
        try {
            var byteStr = atob(dataUrl.split(',')[1]);
            var mimeStr = dataUrl.split(',')[0].split(':')[1].split(';')[0];
            var ab = new ArrayBuffer(byteStr.length);
            var ia = new Uint8Array(ab);
            for (var i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
            var blob = new Blob([ab], { type: mimeStr });
            navigator.clipboard.write([new ClipboardItem({ [mimeStr]: blob })]).catch(function (err) {
                console.warn('[crop] clipboard write failed:', err);
            });
        } catch (err) {
            console.warn('[crop] clipboard copy failed:', err);
        }
    }

    function confirmCrop() {
        var result = cropToDataUrl();
        if (result) {
            copyToClipboard(result);
        }
        close(result);
    }

    function cancelAll() {
        close(null);
    }

    function close(result) {
        // 任何已 in-flight 的 recapture promise 在 then/catch/finally 里都会发现
        // runId 已过期，从而不会再触碰 sourceDataUrl / 按钮文案。
        recaptureRunId++;
        if (overlay) overlay.style.display = 'none';
        sel = null;
        mode = MODE_NONE;
        sourceDataUrl = null;
        recaptureFn = null;
        activeTab = 'screenshot';
        hideActionBtns();

        if (resolvePromise) {
            var fn = resolvePromise;
            resolvePromise = null;
            fn(result);
        }
    }

    // ======================== Resize handling ========================
    function onResize() {
        if (!overlay || overlay.style.display === 'none') return;
        sizeCanvas();
        computeImgMetrics();
        sel = null;
        hideActionBtns();
        drawOverlay();
    }

    function sizeCanvas() {
        canvas.width = overlay.clientWidth;
        canvas.height = overlay.clientHeight - getTopBarHeight();
    }

    // ======================== Image loading ========================
    function loadImage(dataUrl) {
        imgEl.onload = function () {
            sizeCanvas();
            computeImgMetrics();
            sel = null;
            hideActionBtns();
            drawOverlay();
            overlay.focus();
        };
        imgEl.onerror = function () {
            close(null);
        };
        imgEl.src = dataUrl;
    }

    // ======================== Public API ========================
    mod.cropImage = function cropImage(dataUrl, opts) {
        return new Promise(function (resolve) {
            ensureOverlay();
            if (resolvePromise) close(null);

            sourceDataUrl = dataUrl;
            resolvePromise = resolve;
            recaptureFn = (opts && opts.recaptureFn) || null;

            // Reset state
            sel = null;
            mode = MODE_NONE;
            activeTab = 'screenshot';
            // 新会话开始 —— 失效任何尚未结算的旧 recapture promise，并把按钮恢复初始态。
            // close() 已经 ++ 过一次，这里再 ++ 一次保证 cropImage 直接被重复调用
            // （不经过 close）的边角情况也安全。
            recaptureRunId++;
            tabScreenshot.classList.add('crop-tab-active');
            tabHideNeko.classList.remove('crop-tab-active');
            tabHideNeko.style.display = recaptureFn ? '' : 'none';
            tabHideNeko.disabled = false;
            tabHideNeko.textContent = tr('chat.cropTabHideNeko', '\u9690\u85CFNEKO');
            hideActionBtns();

            loadImage(dataUrl);

            overlay.style.display = 'flex';
            overlay.tabIndex = -1;
            overlay.focus();
            window.addEventListener('resize', onResize);
        }).finally(function () {
            window.removeEventListener('resize', onResize);
        });
    };

    // ======================== Export ========================
    window.appCrop = mod;
})();
