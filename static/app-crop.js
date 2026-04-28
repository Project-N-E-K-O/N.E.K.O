/**
 * app-crop.js — Full-screen screenshot crop tool
 *
 * Flow: capture full screen → show overlay → user selects region →
 *       confirm (✓) saves to clipboard + returns dataUrl, cancel (×) clears selection,
 *       top-bar "取消" or right-click exits entirely.
 *
 * Supports: drag to create selection, move selection, resize via corner/edge handles,
 *           keyboard nudging (arrows), Enter confirm, Delete clear.
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
    // 会话开始时的原始截图，"隐藏NEKO" 重截图只更新 sourceDataUrl，
    // 这样切回"截图"页签可以恢复回最初的图，而不是停留在隐藏后的版本。
    var originalDataUrl = null;
    var recaptureFn = null;
    var selectionBox = null;
    var selectionBadge = null;
    var crosshairX = null;
    var crosshairY = null;
    var pointerBadge = null;
    // 单调递增 token：防止旧 recaptureFn 异步返回时把结果灌进新一轮 crop 会话，
    // 或在 finally 里把新会话刚显示的"隐藏NEKO"按钮文案/disabled 状态错误重置。
    var recaptureRunId = 0;
    var renderQueued = false;
    var pointerPos = null;

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

        // ---- Workspace ----
        var workspace = document.createElement('div');
        workspace.className = 'crop-workspace';
        overlay.appendChild(workspace);

        // Background image
        imgEl = document.createElement('img');
        imgEl.className = 'crop-bg-image';
        imgEl.draggable = false;
        workspace.appendChild(imgEl);

        // Canvas
        canvas = document.createElement('canvas');
        canvas.className = 'crop-canvas';
        workspace.appendChild(canvas);
        ctx = canvas.getContext('2d');

        selectionBox = document.createElement('div');
        selectionBox.className = 'crop-selection-box';
        selectionBox.setAttribute('aria-hidden', 'true');
        selectionBox.style.display = 'none';
        for (var i = 0; i < 4; i++) {
            var gridLine = document.createElement('div');
            gridLine.className = 'crop-selection-grid-line ' + (i < 2 ? 'h' + (i + 1) : 'v' + (i - 1));
            selectionBox.appendChild(gridLine);
        }
        var handleNames = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
        for (var j = 0; j < handleNames.length; j++) {
            var handleEl = document.createElement('div');
            handleEl.className = 'crop-selection-handle ' + handleNames[j];
            selectionBox.appendChild(handleEl);
        }
        workspace.appendChild(selectionBox);

        selectionBadge = document.createElement('div');
        selectionBadge.className = 'crop-selection-badge';
        selectionBadge.style.display = 'none';
        workspace.appendChild(selectionBadge);

        crosshairX = document.createElement('div');
        crosshairX.className = 'crop-crosshair crop-crosshair-x';
        crosshairX.style.display = 'none';
        workspace.appendChild(crosshairX);

        crosshairY = document.createElement('div');
        crosshairY.className = 'crop-crosshair crop-crosshair-y';
        crosshairY.style.display = 'none';
        workspace.appendChild(crosshairY);

        pointerBadge = document.createElement('div');
        pointerBadge.className = 'crop-pointer-badge';
        pointerBadge.style.display = 'none';
        workspace.appendChild(pointerBadge);

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
        canvas.addEventListener('mouseleave', onPointerLeave);
        canvas.addEventListener('dblclick', onDoubleClick);
        canvas.addEventListener('touchstart', onTouchStart, { passive: false });
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onTouchEnd);

        // Right-click to cancel entirely
        canvas.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            cancelAll();
        });

        overlay.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                e.preventDefault();
                cancelAll();
                return;
            }
            var target = e.target;
            if (
                target
                && (
                    target.tagName === 'BUTTON'
                    || target.tagName === 'INPUT'
                    || target.tagName === 'TEXTAREA'
                    || target.tagName === 'SELECT'
                    || target.isContentEditable
                )
            ) {
                return;
            }
            if ((e.key === 'Delete' || e.key === 'Backspace') && sel) {
                e.preventDefault();
                clearSelection();
                return;
            }
            if ((e.key === 'Enter' || e.key === 'NumpadEnter') && sel) {
                e.preventDefault();
                confirmCrop();
                return;
            }
            if (!sel) return;
            var step = e.shiftKey ? 10 : 1;
            var handled = true;
            if (e.key === 'ArrowLeft') {
                moveSelectionBy(-step, 0);
            } else if (e.key === 'ArrowRight') {
                moveSelectionBy(step, 0);
            } else if (e.key === 'ArrowUp') {
                moveSelectionBy(0, -step);
            } else if (e.key === 'ArrowDown') {
                moveSelectionBy(0, step);
            } else {
                handled = false;
            }
            if (handled) {
                e.preventDefault();
            }
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
        if (overlay) {
            overlay.focus();
        }

        if (tab === 'screenshot' && originalDataUrl && sourceDataUrl !== originalDataUrl) {
            sourceDataUrl = originalDataUrl;
            loadImage(originalDataUrl);
            return;
        }

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
    function computeImgMetrics() {
        var overlayW = overlay.clientWidth;
        var overlayH = overlay.clientHeight;
        var natW = imgEl.naturalWidth;
        var natH = imgEl.naturalHeight;
        imgNaturalWidth = natW;
        imgNaturalHeight = natH;

        // 移除 1 的上限，让低分辨率截图在高分屏上也能放大填满容器，
        // 避免周围出现大面积黑边导致用户误以为边缘内容"选不到"。
        var scale = Math.min(overlayW / natW, overlayH / natH);
        imgDisplayWidth = Math.round(natW * scale);
        imgDisplayHeight = Math.round(natH * scale);
        imgDisplayLeft = Math.round((overlayW - imgDisplayWidth) / 2);
        imgDisplayTop = Math.round((overlayH - imgDisplayHeight) / 2);

        // 同步 DOM 图片尺寸和位置，确保 CSS 显示和 canvas 计算完全一致
        if (imgEl) {
            imgEl.style.width = imgDisplayWidth + 'px';
            imgEl.style.height = imgDisplayHeight + 'px';
            imgEl.style.left = imgDisplayLeft + 'px';
            imgEl.style.top = imgDisplayTop + 'px';
        }
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

    function clampPointToImage(x, y) {
        var right = imgDisplayLeft + imgDisplayWidth;
        var bottom = imgDisplayTop + imgDisplayHeight;
        return {
            x: Math.max(imgDisplayLeft, Math.min(right, x)),
            y: Math.max(imgDisplayTop, Math.min(bottom, y))
        };
    }

    function isPointWithinImage(x, y) {
        return x >= imgDisplayLeft
            && x <= imgDisplayLeft + imgDisplayWidth
            && y >= imgDisplayTop
            && y <= imgDisplayTop + imgDisplayHeight;
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
        ctx.setLineDash([8, 4]);
        ctx.strokeRect(cs.x, cs.y, cs.w, cs.h);
        ctx.setLineDash([]);
    }

    // ======================== Action buttons position ========================
    function updateSelectionUI() {
        if (!selectionBox || !selectionBadge || !actionBtns) return;
        if (!sel) {
            selectionBox.style.display = 'none';
            selectionBadge.style.display = 'none';
            actionBtns.style.display = 'none';
            return;
        }
        var cs = clampSel(sel);
        if (!cs) {
            selectionBox.style.display = 'none';
            selectionBadge.style.display = 'none';
            actionBtns.style.display = 'none';
            return;
        }

        selectionBox.style.display = 'block';
        selectionBox.style.left = cs.x + 'px';
        selectionBox.style.top = cs.y + 'px';
        selectionBox.style.width = cs.w + 'px';
        selectionBox.style.height = cs.h + 'px';

        var c1 = canvasToImage(cs.x, cs.y);
        var c2 = canvasToImage(cs.x + cs.w, cs.y + cs.h);
        var cropW = Math.round(Math.abs(c2.x - c1.x));
        var cropH = Math.round(Math.abs(c2.y - c1.y));
        selectionBadge.textContent = cropW + ' × ' + cropH;
        selectionBadge.style.display = 'block';
        selectionBadge.style.left = cs.x + 'px';
        selectionBadge.style.top = Math.max(12, cs.y - 36) + 'px';

        actionBtns.style.display = 'flex';
        var btnW = actionBtns.offsetWidth || 92;
        var btnH = actionBtns.offsetHeight || 40;
        var left = cs.x + cs.w - btnW;
        var top = cs.y + cs.h + 12;

        // If overflows bottom, put above selection
        if (top + btnH > overlay.clientHeight - 12) {
            top = cs.y - btnH - 12;
        }
        // Clamp to viewport
        if (left < 12) left = 12;
        if (left + btnW > overlay.clientWidth - 12) left = overlay.clientWidth - btnW - 12;
        if (top < 12) top = 12;

        actionBtns.style.left = left + 'px';
        actionBtns.style.top = top + 'px';
    }

    function updatePointerUI() {
        if (!crosshairX || !crosshairY || !pointerBadge) return;
        if (!pointerPos || !isPointWithinImage(pointerPos.x, pointerPos.y)) {
            crosshairX.style.display = 'none';
            crosshairY.style.display = 'none';
            pointerBadge.style.display = 'none';
            return;
        }

        var showCrosshair = !sel || mode === MODE_NEW;
        crosshairX.style.display = showCrosshair ? 'block' : 'none';
        crosshairY.style.display = showCrosshair ? 'block' : 'none';
        pointerBadge.style.display = showCrosshair ? 'block' : 'none';

        if (!showCrosshair) {
            return;
        }

        crosshairX.style.top = pointerPos.y + 'px';
        crosshairY.style.left = pointerPos.x + 'px';
        var imgPoint = canvasToImage(pointerPos.x, pointerPos.y);
        pointerBadge.textContent = Math.max(0, Math.round(imgPoint.x)) + ', ' + Math.max(0, Math.round(imgPoint.y));
        pointerBadge.style.left = Math.min(overlay.clientWidth - 88, pointerPos.x + 18) + 'px';
        pointerBadge.style.top = Math.max(12, pointerPos.y - 34) + 'px';
    }

    function requestRender() {
        if (renderQueued) return;
        renderQueued = true;
        requestAnimationFrame(function () {
            renderQueued = false;
            drawOverlay();
            updateSelectionUI();
            updatePointerUI();
        });
    }

    // ======================== Pointer events ========================
    function onPointerDown(e) {
        if (e.button === 2) return; // right-click handled by contextmenu
        e.preventDefault();
        if (overlay) {
            overlay.focus();
        }
        var pos = getPointerPos(e);
        pointerPos = pos;

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
        var startPos = clampPointToImage(pos.x, pos.y);
        mode = MODE_NEW;
        dragStartX = startPos.x;
        dragStartY = startPos.y;
        sel = { x: startPos.x, y: startPos.y, w: 0, h: 0 };
        hideActionBtns();
        requestRender();
    }

    function onPointerMove(e) {
        if (!canvas || !overlay || overlay.style.display === 'none') return;
        var pos = getPointerPos(e);
        pointerPos = pos;
        if (mode === MODE_NONE) {
            // Update cursor based on hover
            var h = hitTestHandle(pos.x, pos.y);
            if (h) {
                canvas.style.cursor = getCursorForHandle(h);
            } else if (hitTestInside(pos.x, pos.y)) {
                canvas.style.cursor = 'move';
            } else {
                canvas.style.cursor = 'crosshair';
            }
            requestRender();
            return;
        }

        e.preventDefault();
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

        requestRender();
    }

    function onPointerUp(e) {
        if (mode === MODE_NONE) return;
        var prevMode = mode;
        mode = MODE_NONE;
        var pos = getPointerPos(e);
        pointerPos = pos;

        if (prevMode === MODE_NEW) {
            sel = normRect(dragStartX, dragStartY, pos.x, pos.y);
        }

        // Validate selection
        var cs = clampSel(sel);
        if (!cs || cs.w < MIN_SEL || cs.h < MIN_SEL) {
            sel = null;
            hideActionBtns();
        } else {
            sel = cs;
        }
        requestRender();
    }

    function onPointerLeave() {
        if (mode !== MODE_NONE) return;
        pointerPos = null;
        updatePointerUI();
    }

    function onDoubleClick(e) {
        if (!sel) return;
        var pos = getPointerPos(e);
        if (hitTestInside(pos.x, pos.y)) {
            e.preventDefault();
            confirmCrop();
        }
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

    function moveSelectionBy(dx, dy) {
        if (!sel) return;
        sel = {
            x: sel.x + dx,
            y: sel.y + dy,
            w: sel.w,
            h: sel.h
        };
        if (sel.x < imgDisplayLeft) sel.x = imgDisplayLeft;
        if (sel.y < imgDisplayTop) sel.y = imgDisplayTop;
        if (sel.x + sel.w > imgDisplayLeft + imgDisplayWidth) sel.x = imgDisplayLeft + imgDisplayWidth - sel.w;
        if (sel.y + sel.h > imgDisplayTop + imgDisplayHeight) sel.y = imgDisplayTop + imgDisplayHeight - sel.h;
        requestRender();
    }

    // ======================== Actions ========================
    function hideActionBtns() {
        if (actionBtns) actionBtns.style.display = 'none';
    }

    function clearSelection() {
        sel = null;
        mode = MODE_NONE;
        hideActionBtns();
        requestRender();
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
        originalDataUrl = null;
        recaptureFn = null;
        activeTab = 'screenshot';
        pointerPos = null;
        hideActionBtns();
        if (selectionBox) selectionBox.style.display = 'none';
        if (selectionBadge) selectionBadge.style.display = 'none';
        if (pointerBadge) pointerBadge.style.display = 'none';
        if (crosshairX) crosshairX.style.display = 'none';
        if (crosshairY) crosshairY.style.display = 'none';

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
        requestRender();
    }

    function sizeCanvas() {
        canvas.width = overlay.clientWidth;
        canvas.height = overlay.clientHeight;
    }

    // ======================== Image loading ========================
    function loadImage(dataUrl) {
        imgEl.onload = function () {
            sizeCanvas();
            computeImgMetrics();
            sel = null;
            hideActionBtns();
            requestRender();
            overlay.focus();
        };
        imgEl.onerror = function () {
            close(null);
        };
        imgEl.src = dataUrl;
    }

    // ======================== Public API ========================
    mod.cropImage = function cropImage(dataUrl, opts) {
        var sessionResizeHandler = null;
        return new Promise(function (resolve) {
            ensureOverlay();
            if (resolvePromise) close(null);

            sourceDataUrl = dataUrl;
            originalDataUrl = dataUrl;
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
            sessionResizeHandler = function () {
                onResize();
            };
            window.addEventListener('resize', sessionResizeHandler);
        }).finally(function () {
            if (sessionResizeHandler) {
                window.removeEventListener('resize', sessionResizeHandler);
                sessionResizeHandler = null;
            }
        });
    };

    // ======================== Export ========================
    window.appCrop = mod;
})();
