/**
 * app-crop.js — Screen region crop overlay
 *
 * After a full-screen screenshot is captured, this module shows an overlay
 * letting the user drag-select a rectangular region, then crops and returns
 * the selected area as a data URL.
 *
 * Exports: window.appCrop
 *   - window.appCrop.cropImage(dataUrl) → Promise<string|null>
 *     Shows the overlay with the given image; resolves with cropped dataUrl
 *     or null if the user cancels.
 */
(function () {
    'use strict';

    var mod = {};

    // ======================== State ========================
    var overlay = null;       // the full-screen overlay element
    var canvas = null;        // drawing canvas (selection rectangle)
    var ctx = null;
    var imgEl = null;         // the background <img> in the overlay
    var resolvePromise = null;
    var sourceDataUrl = null;

    // Selection coordinates (relative to image display area)
    var selecting = false;
    var startX = 0, startY = 0;
    var curX = 0, curY = 0;

    // Image display metrics (computed once image loads)
    var imgDisplayLeft = 0, imgDisplayTop = 0;
    var imgDisplayWidth = 0, imgDisplayHeight = 0;
    var imgNaturalWidth = 0, imgNaturalHeight = 0;

    // ======================== Ensure DOM ========================
    function ensureOverlay() {
        if (overlay) return;

        overlay = document.createElement('div');
        overlay.id = 'crop-overlay';
        overlay.className = 'crop-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', '\u622A\u56FE\u88C1\u5207');
        overlay.style.display = 'none';

        // Background image (the full screenshot)
        imgEl = document.createElement('img');
        imgEl.className = 'crop-bg-image';
        imgEl.draggable = false;
        overlay.appendChild(imgEl);

        // Canvas for drawing the selection rectangle + dimming mask
        canvas = document.createElement('canvas');
        canvas.className = 'crop-canvas';
        overlay.appendChild(canvas);
        ctx = canvas.getContext('2d');

        // Toolbar
        var toolbar = document.createElement('div');
        toolbar.className = 'crop-toolbar';

        var hint = document.createElement('span');
        hint.className = 'crop-hint';
        hint.textContent = '\u62D6\u62FD\u9009\u53D6\u533A\u57DF\uFF0C\u677E\u5F00\u540E\u786E\u8BA4';
        toolbar.appendChild(hint);

        var btnGroup = document.createElement('span');
        btnGroup.className = 'crop-btn-group';

        var fullBtn = document.createElement('button');
        fullBtn.className = 'crop-btn crop-btn-full';
        fullBtn.textContent = '\u53D1\u9001\u5168\u5C4F';
        fullBtn.type = 'button';
        fullBtn.addEventListener('click', confirmFull);

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'crop-btn crop-btn-cancel';
        cancelBtn.textContent = '\u53D6\u6D88';
        cancelBtn.type = 'button';
        cancelBtn.addEventListener('click', cancel);

        var confirmBtn = document.createElement('button');
        confirmBtn.className = 'crop-btn crop-btn-confirm';
        confirmBtn.textContent = '\u786E\u8BA4\u88C1\u5207';
        confirmBtn.type = 'button';
        confirmBtn.addEventListener('click', confirmCrop);

        btnGroup.appendChild(fullBtn);
        btnGroup.appendChild(cancelBtn);
        btnGroup.appendChild(confirmBtn);
        toolbar.appendChild(btnGroup);
        overlay.appendChild(toolbar);

        // Store references for confirm button toggling
        overlay._confirmBtn = confirmBtn;
        overlay._hint = hint;

        // Events
        canvas.addEventListener('mousedown', onPointerDown);
        canvas.addEventListener('mousemove', onPointerMove);
        canvas.addEventListener('mouseup', onPointerUp);
        canvas.addEventListener('touchstart', onTouchStart, { passive: false });
        canvas.addEventListener('touchmove', onTouchMove, { passive: false });
        canvas.addEventListener('touchend', onTouchEnd);

        // Escape to cancel
        overlay.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') cancel();
        });

        document.body.appendChild(overlay);
    }

    // ======================== Coordinate helpers ========================
    function computeImgMetrics() {
        // The image is displayed with object-fit:contain style.
        // Compute its actual rendered position within the overlay.
        var overlayW = overlay.clientWidth;
        var overlayH = overlay.clientHeight - 52; // subtract toolbar height
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
        // Convert canvas coords to natural image coords
        var ix = (cx - imgDisplayLeft) / imgDisplayWidth * imgNaturalWidth;
        var iy = (cy - imgDisplayTop) / imgDisplayHeight * imgNaturalHeight;
        return { x: ix, y: iy };
    }

    function getPointerPos(e) {
        var rect = canvas.getBoundingClientRect();
        return {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top
        };
    }

    // ======================== Selection drawing ========================
    function drawOverlay() {
        if (!ctx || !canvas) return;
        var w = canvas.width;
        var h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        // Semi-transparent dark mask over entire canvas
        ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        ctx.fillRect(0, 0, w, h);

        if (!selecting && startX === 0 && startY === 0 && curX === 0 && curY === 0) {
            // No selection yet — full image visible through lighter mask
            // Clear image display area to show image more clearly
            ctx.clearRect(imgDisplayLeft, imgDisplayTop, imgDisplayWidth, imgDisplayHeight);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
            ctx.fillRect(imgDisplayLeft, imgDisplayTop, imgDisplayWidth, imgDisplayHeight);
            return;
        }

        // Compute selection rectangle
        var sx = Math.min(startX, curX);
        var sy = Math.min(startY, curY);
        var sw = Math.abs(curX - startX);
        var sh = Math.abs(curY - startY);

        // Clamp to image display area
        var clampRight = imgDisplayLeft + imgDisplayWidth;
        var clampBottom = imgDisplayTop + imgDisplayHeight;
        if (sx < imgDisplayLeft) { sw -= (imgDisplayLeft - sx); sx = imgDisplayLeft; }
        if (sy < imgDisplayTop) { sh -= (imgDisplayTop - sy); sy = imgDisplayTop; }
        if (sx + sw > clampRight) sw = clampRight - sx;
        if (sy + sh > clampBottom) sh = clampBottom - sy;
        if (sw < 0) sw = 0;
        if (sh < 0) sh = 0;

        // Clear the selected region to reveal the image underneath
        if (sw > 0 && sh > 0) {
            ctx.clearRect(sx, sy, sw, sh);

            // Draw selection border
            ctx.strokeStyle = '#44b7fe';
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 3]);
            ctx.strokeRect(sx, sy, sw, sh);
            ctx.setLineDash([]);

            // Draw corner handles
            drawCornerHandles(sx, sy, sw, sh);

            // Draw dimension label
            var imgCoord1 = canvasToImage(sx, sy);
            var imgCoord2 = canvasToImage(sx + sw, sy + sh);
            var cropW = Math.round(Math.abs(imgCoord2.x - imgCoord1.x));
            var cropH = Math.round(Math.abs(imgCoord2.y - imgCoord1.y));
            if (cropW > 0 && cropH > 0) {
                var label = cropW + ' \u00D7 ' + cropH;
                ctx.font = '12px sans-serif';
                var metrics = ctx.measureText(label);
                var lx = sx + sw / 2 - metrics.width / 2 - 4;
                var ly = sy + sh + 20;
                if (ly > canvas.height - 60) ly = sy - 10; // flip above if near bottom

                ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
                var rw = metrics.width + 8, rh = 20, rx = lx, ry = ly - 14, rr = 4;
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
    }

    function drawCornerHandles(sx, sy, sw, sh) {
        var size = 8;
        ctx.fillStyle = '#44b7fe';
        // top-left
        ctx.fillRect(sx - size / 2, sy - size / 2, size, size);
        // top-right
        ctx.fillRect(sx + sw - size / 2, sy - size / 2, size, size);
        // bottom-left
        ctx.fillRect(sx - size / 2, sy + sh - size / 2, size, size);
        // bottom-right
        ctx.fillRect(sx + sw - size / 2, sy + sh - size / 2, size, size);
    }

    // ======================== Pointer events ========================
    function onPointerDown(e) {
        e.preventDefault();
        var pos = getPointerPos(e);
        startX = pos.x;
        startY = pos.y;
        curX = pos.x;
        curY = pos.y;
        selecting = true;
        updateConfirmBtnState(false);
        drawOverlay();
    }

    function onPointerMove(e) {
        if (!selecting) return;
        e.preventDefault();
        var pos = getPointerPos(e);
        curX = pos.x;
        curY = pos.y;
        drawOverlay();
    }

    function onPointerUp(e) {
        if (!selecting) return;
        selecting = false;
        var pos = getPointerPos(e);
        curX = pos.x;
        curY = pos.y;
        drawOverlay();

        // Check if selection is big enough (at least 10px in each direction)
        var sw = Math.abs(curX - startX);
        var sh = Math.abs(curY - startY);
        if (sw >= 10 && sh >= 10) {
            updateConfirmBtnState(true);
        } else {
            // Too small, reset
            startX = startY = curX = curY = 0;
            updateConfirmBtnState(false);
            drawOverlay();
        }
    }

    // Touch event adapters
    function onTouchStart(e) {
        if (e.touches.length !== 1) return;
        e.preventDefault();
        var touch = e.touches[0];
        onPointerDown({ preventDefault: function () {}, clientX: touch.clientX, clientY: touch.clientY });
    }

    function onTouchMove(e) {
        if (e.touches.length !== 1) return;
        e.preventDefault();
        var touch = e.touches[0];
        onPointerMove({ preventDefault: function () {}, clientX: touch.clientX, clientY: touch.clientY });
    }

    function onTouchEnd(e) {
        var touch = e.changedTouches[0];
        onPointerUp({ clientX: touch.clientX, clientY: touch.clientY });
    }

    // ======================== Confirm / Cancel ========================
    function updateConfirmBtnState(hasSelection) {
        if (!overlay) return;
        var btn = overlay._confirmBtn;
        var hint = overlay._hint;
        if (hasSelection) {
            btn.disabled = false;
            btn.classList.add('active');
            hint.textContent = '\u62D6\u62FD\u8C03\u6574\u9009\u533A\uFF0C\u6216\u70B9\u51FB\u201C\u786E\u8BA4\u88C1\u5207\u201D';
        } else {
            btn.disabled = true;
            btn.classList.remove('active');
            hint.textContent = '\u62D6\u62FD\u9009\u53D6\u533A\u57DF\uFF0C\u677E\u5F00\u540E\u786E\u8BA4';
        }
    }

    function getSelectionRect() {
        // Returns clamped selection in image-display coordinates
        var sx = Math.min(startX, curX);
        var sy = Math.min(startY, curY);
        var sw = Math.abs(curX - startX);
        var sh = Math.abs(curY - startY);

        // Clamp to image display area
        var clampRight = imgDisplayLeft + imgDisplayWidth;
        var clampBottom = imgDisplayTop + imgDisplayHeight;
        if (sx < imgDisplayLeft) { sw -= (imgDisplayLeft - sx); sx = imgDisplayLeft; }
        if (sy < imgDisplayTop) { sh -= (imgDisplayTop - sy); sy = imgDisplayTop; }
        if (sx + sw > clampRight) sw = clampRight - sx;
        if (sy + sh > clampBottom) sh = clampBottom - sy;
        if (sw < 1 || sh < 1) return null;

        return { x: sx, y: sy, w: sw, h: sh };
    }

    function cropToDataUrl() {
        var sel = getSelectionRect();
        if (!sel) return null;

        // Map display coords → natural image coords
        var img1 = canvasToImage(sel.x, sel.y);
        var img2 = canvasToImage(sel.x + sel.w, sel.y + sel.h);
        var cx = Math.max(0, Math.round(img1.x));
        var cy = Math.max(0, Math.round(img1.y));
        var cw = Math.min(imgNaturalWidth - cx, Math.round(img2.x - img1.x));
        var ch = Math.min(imgNaturalHeight - cy, Math.round(img2.y - img1.y));
        if (cw < 1 || ch < 1) return null;

        // Draw cropped region to a temp canvas
        var tmpCanvas = document.createElement('canvas');
        tmpCanvas.width = cw;
        tmpCanvas.height = ch;
        var tmpCtx = tmpCanvas.getContext('2d');
        tmpCtx.drawImage(imgEl, cx, cy, cw, ch, 0, 0, cw, ch);
        return tmpCanvas.toDataURL('image/jpeg', 0.9);
    }

    function confirmCrop() {
        var result = cropToDataUrl();
        close(result);
    }

    function confirmFull() {
        // Send full screenshot without cropping
        close(sourceDataUrl);
    }

    function cancel() {
        close(null);
    }

    function close(result) {
        if (overlay) {
            overlay.style.display = 'none';
        }
        // Reset selection state
        selecting = false;
        startX = startY = curX = curY = 0;
        sourceDataUrl = null;

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
        // Reset selection since coordinates are now invalid
        startX = startY = curX = curY = 0;
        updateConfirmBtnState(false);
        drawOverlay();
    }

    function sizeCanvas() {
        canvas.width = overlay.clientWidth;
        canvas.height = overlay.clientHeight - 52; // subtract toolbar
    }

    // ======================== Public API ========================
    /**
     * Show the crop overlay with the given full-screen screenshot.
     * @param {string} dataUrl - the full screenshot data URL
     * @returns {Promise<string|null>} - cropped data URL, full data URL, or null if cancelled
     */
    mod.cropImage = function cropImage(dataUrl) {
        return new Promise(function (resolve) {
            ensureOverlay();
            sourceDataUrl = dataUrl;
            resolvePromise = resolve;

            // Reset state
            selecting = false;
            startX = startY = curX = curY = 0;
            updateConfirmBtnState(false);

            // Load image
            imgEl.onload = function () {
                sizeCanvas();
                computeImgMetrics();
                drawOverlay();
                overlay.focus();
            };
            imgEl.src = dataUrl;

            overlay.style.display = 'flex';
            overlay.tabIndex = -1;
            overlay.focus();

            // Listen for resize
            window.addEventListener('resize', onResize);
        }).finally(function () {
            window.removeEventListener('resize', onResize);
        });
    };

    // ======================== Export ========================
    window.appCrop = mod;
})();
