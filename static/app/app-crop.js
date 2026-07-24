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
 *   - cropImage(dataUrl, opts) → Promise<string|{action,dataUrl}|null>
 *     opts.recaptureFn: async () => dataUrl  (called by "隐藏NEKO" tab)
 *     opts.rightClickCancelsAll: true  (system-style overlay: right click exits immediately)
 *     opts.allowPin: true  (show the desktop pin action in a PC-owned overlay)
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
    var onRecaptureImageReady = null;
    var rightClickCancelsAll = false;
    var allowPin = false;
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
    // (旧的独立 ✓/× actionBtns 已并入标注工具栏 toolbarEl)
    var tabScreenshot = null;
    var tabHideNeko = null;
    var activeTab = 'screenshot'; // 'screenshot' | 'hideNeko'

    var HANDLE_SIZE = 8;
    var MIN_SEL = 10;

    // ======================== Annotation state ========================
    // 标注全部以"图片自然坐标"存储，渲染时经 mapFn 映射到目标画布。
    // 这样窗口 resize、选区 resize 都不破坏标注，烤制时只是坐标平移。
    var currentTool = 'select'; // 'select'|'rect'|'ellipse'|'arrow'|'pen'|'highlighter'|'text'|'mosaic'|'watermark'
    var DRAW_TOOLS = { rect: 1, ellipse: 1, arrow: 1, pen: 1, highlighter: 1, text: 1, mosaic: 1, watermark: 1 };
    // 选中后会浮出上下文选项条的工具（每个工具有各自可调属性）
    var OPTION_TOOLS = { text: 1, highlighter: 1, mosaic: 1, watermark: 1 };
    var currentColor = '#ff3b30';
    var currentStrokeWidth = 4; // 图片自然坐标下的线宽基准
    var annotations = [];       // 当前工作数组（渲染/命中测试都读它，始终等于 history[historyIndex] 的内容）
    // 快照式撤销历史：每次提交存一份 annotations 浅拷贝；undo/redo 切快照。
    // 标注对象提交后视为不可变（改水印=替换为新对象，不原地改），所以浅拷贝即安全。
    // 相比 pop 式，能正确表达"撤销栈中间某个标注的编辑/删除"。
    var history = [[]];         // 快照栈，初始一份空快照
    var historyIndex = 0;       // 当前快照下标
    var lastHistoryTag = null;  // 上一步的类型标签，用于合并连续同类编辑（如水印逐字输入）
    var annoDraft = null;       // 正在绘制的草稿（独立于 mode 选区状态机）
    var textEditor = null;      // 文字工具的 <textarea> DOM
    var workspaceEl = null;     // .crop-workspace 容器（textarea 挂这里）
    var toolbarEl = null;       // 标注工具栏 DOM
    var pinBtn = null;          // PC 独立截图层才显示的“贴图”动作
    var optionsBarEl = null;    // 上下文选项条 DOM（随当前工具刷新内容）
    var toolBtns = {};          // name -> button，用于切换 active 态
    var colorSwatches = [];     // 调色板按钮
    var widthBtns = [];         // 线宽按钮
    var undoBtn = null, redoBtn = null;

    // 各工具的可调属性（默认值见下方常量）。文字/水印字号以"屏幕显示 px"为基准，
    // 提交时按 displayScale 换算成图片自然坐标 px 存进标注，保证抗 resize。
    var currentFontSizePx = 22;       // 文字工具屏幕字号
    var currentHighlightAlpha = 0.38; // 荧光笔透明度（= HIGHLIGHT_ALPHA 默认）
    var currentMosaicBlock = 12;      // 马赛克块大小（图片自然坐标 px，= MOSAIC_BLOCK 默认）
    var currentWatermarkText = '';    // 水印文字（空则用默认占位）
    var currentWatermarkSizePx = 30;  // 水印屏幕字号
    // 水印有独立的"粘性"颜色（仿 QQ），与全局绘图色 currentColor 解耦：调色板在水印工具下
    // 改的是它，undo/redo 同步的也是它，绝不污染 pen/箭头等用的 currentColor。
    var currentWatermarkColor = '#ff3b30';

    var PALETTE = ['#ff3b30', '#ffcc00', '#34c759', '#0a84ff', '#ffffff', '#1c1c1e'];
    // 与 PALETTE 同序：调色板按钮的无障碍可读名（纯色按钮无文字，读屏靠这个）
    var COLOR_LABELS = [
        ['chat.cropColorRed', '红色'],
        ['chat.cropColorYellow', '黄色'],
        ['chat.cropColorGreen', '绿色'],
        ['chat.cropColorBlue', '蓝色'],
        ['chat.cropColorWhite', '白色'],
        ['chat.cropColorBlack', '黑色']
    ];
    var WIDTH_PRESETS = [2, 4, 8];     // 图片坐标线宽：S / M / L
    var MOSAIC_BLOCK = 12;             // 马赛克块大小（图片自然坐标 px）
    var HIGHLIGHT_ALPHA = 0.38;
    var HIGHLIGHT_WIDTH_MULT = 4;

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
        workspaceEl = workspace;

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

        // ---- Annotation toolbar (tools + style + actions, includes ✓ / ×) ----
        ensureToolbar();
        overlay.appendChild(toolbarEl);

        // ---- Contextual options bar (per-tool extras: font size / opacity / mosaic / watermark) ----
        ensureOptionsBar();
        overlay.appendChild(optionsBarEl);

        // ---- Events ----
        canvas.addEventListener('mousedown', onPointerDown);
        document.addEventListener('mousemove', onPointerMove);
        document.addEventListener('mouseup', onPointerUp);
        canvas.addEventListener('mouseleave', onPointerLeave);
        canvas.addEventListener('dblclick', onDoubleClick);
        canvas.addEventListener('touchstart', onTouchStart, { passive: false });
        document.addEventListener('touchmove', onTouchMove, { passive: false });
        document.addEventListener('touchend', onTouchEnd);

        // Right-click behaviour depends on the active tool
        canvas.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            if (rightClickCancelsAll) {
                cancelAll();
                return;
            }
            // 绘图进行中：丢弃当前草稿，不关遮罩
            if (annoDraft) {
                annoDraft = null;
                requestRender();
                return;
            }
            // 绘图工具但无草稿：退回选择工具（Snipaste 式）
            if (DRAW_TOOLS[currentTool]) {
                setTool('select');
                return;
            }
            // 选择工具下右键 = 整体取消
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
            // Ctrl/Cmd+Z 撤销，Ctrl+Y / Ctrl+Shift+Z 重做
            if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
                e.preventDefault();
                if (e.shiftKey) redo(); else undo();
                return;
            }
            if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y')) {
                e.preventDefault();
                redo();
                return;
            }
            if ((e.key === 'Delete' || e.key === 'Backspace') && sel) {
                e.preventDefault();
                // 有标注先撤最后一笔，没标注才清整个选区，避免误删全部标注
                if (annotations.length) undo();
                else clearSelection();
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
                    loadImage(newDataUrl, function () {
                        if (runId !== recaptureRunId || activeTab !== 'hideNeko' || !onRecaptureImageReady) return;
                        Promise.resolve(onRecaptureImageReady()).catch(function () {});
                    });
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

        // 【清晰度关键】当截图尺寸 ≈ 裁剪层尺寸（全屏截图铺在同尺寸屏上，最常见）时，
        // overlay 常因 1px 边框/取整比图小一两像素，scale 会是 0.999 这种接近 1 的【分数】，
        // 使整张图被按非整数倍做一次双线性重采样 —— 每条文字/UI 边缘都被糊上 1px 混合，
        // 全图肉眼明显发虚（与浏览器非整数缩放发糊同源）。这里把"图≈层"（两个维度都只差
        // 几像素）的情形【吸附成精确 1:1】，让全屏截图像素级清晰；多出的 1~2px 由
        // .crop-workspace 的 overflow:hidden 裁掉，不可见。
        // 只吸附近似 1:1：明显更大的图（如 4K 截到 1080p 层）仍按 scale<1 真实降采样；
        // 明显更小的图仍按 scale>1 放大铺满 —— 既有行为不变。
        var SNAP_PX = 4;
        if (Math.abs(natW - overlayW) <= SNAP_PX && Math.abs(natH - overlayH) <= SNAP_PX) {
            scale = 1;
        }

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

    // 逆映射：图片自然坐标 -> 遮罩 canvas 显示坐标
    function imageToCanvas(ix, iy) {
        return {
            x: imgDisplayLeft + ix / imgNaturalWidth * imgDisplayWidth,
            y: imgDisplayTop + iy / imgNaturalHeight * imgDisplayHeight
        };
    }

    // live 渲染时图片->显示的缩放系数（线宽/字号按此缩放）；烤制时为 1
    function displayScale() {
        if (!imgNaturalWidth) return 1;
        return imgDisplayWidth / imgNaturalWidth;
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

    // 把指针 clamp 到当前选区范围内（绘图草稿用，超出选区即贴边）
    function clampPointToSel(x, y) {
        if (!sel) return clampPointToImage(x, y);
        return {
            x: Math.max(sel.x, Math.min(sel.x + sel.w, x)),
            y: Math.max(sel.y, Math.min(sel.y + sel.h, y))
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

    // ======================== Annotation rendering ========================
    // 规范化 rect 型标注（x0,y0,x1,y1 -> x,y,w,h，自然坐标）
    function normAnno(a) {
        return {
            x: Math.min(a.x0, a.x1),
            y: Math.min(a.y0, a.y1),
            w: Math.abs(a.x1 - a.x0),
            h: Math.abs(a.y1 - a.y0)
        };
    }

    // 把图片区域 (ix,iy,iw,ih 自然坐标) 像素化进一张自然分辨率缓存 canvas。
    // 用于马赛克：源永远取 imgEl（自然分辨率），不取被遮罩压暗的显示 canvas。
    function buildMosaicCache(ix, iy, iw, ih, block) {
        iw = Math.round(iw); ih = Math.round(ih);
        if (iw < 1 || ih < 1) return null;
        var blk = block || MOSAIC_BLOCK;
        try {
            var smallW = Math.max(1, Math.round(iw / blk));
            var smallH = Math.max(1, Math.round(ih / blk));
            var small = document.createElement('canvas');
            small.width = smallW; small.height = smallH;
            var sctx = small.getContext('2d');
            sctx.imageSmoothingEnabled = true;
            sctx.drawImage(imgEl, Math.round(ix), Math.round(iy), iw, ih, 0, 0, smallW, smallH);

            var cache = document.createElement('canvas');
            cache.width = iw; cache.height = ih;
            var cctx = cache.getContext('2d');
            cctx.imageSmoothingEnabled = false;
            cctx.drawImage(small, 0, 0, smallW, smallH, 0, 0, iw, ih);
            return cache;
        } catch (err) {
            // imgEl 万一被 taint（理论上 dataURL 不会），降级为半透明灰块
            console.warn('[crop] mosaic sample failed:', err);
            return null;
        }
    }

    function renderOneAnnotation(c, a, map, scale) {
        var col = a.color || currentColor;
        var lw = (a.width || currentStrokeWidth) * scale;
        if (a.type === 'pen' || a.type === 'highlighter') {
            var pts = a.points;
            if (!pts || pts.length === 0) return;
            c.save();
            c.strokeStyle = col;
            c.lineJoin = 'round';
            c.lineCap = 'round';
            if (a.type === 'highlighter') {
                c.globalAlpha = (a.alpha != null) ? a.alpha : HIGHLIGHT_ALPHA;
                c.lineWidth = lw * HIGHLIGHT_WIDTH_MULT;
                c.lineCap = 'butt';
            } else {
                c.lineWidth = lw;
            }
            c.beginPath();
            var p0 = map(pts[0].x, pts[0].y);
            c.moveTo(p0.x, p0.y);
            if (pts.length === 1) {
                c.lineTo(p0.x + 0.1, p0.y + 0.1); // 单点也留个点
            } else {
                for (var i = 1; i < pts.length; i++) {
                    var p = map(pts[i].x, pts[i].y);
                    c.lineTo(p.x, p.y);
                }
            }
            c.stroke();
            c.restore();
            return;
        }
        if (a.type === 'rect' || a.type === 'ellipse' || a.type === 'mosaic') {
            var n = normAnno(a);
            var tl = map(n.x, n.y);
            var br = map(n.x + n.w, n.y + n.h);
            var rx = tl.x, ry = tl.y, rw = br.x - tl.x, rh = br.y - tl.y;
            if (a.type === 'mosaic') {
                var cache = a._cache || buildMosaicCache(n.x, n.y, n.w, n.h, a.block);
                c.save();
                if (cache) {
                    c.imageSmoothingEnabled = false;
                    c.drawImage(cache, rx, ry, rw, rh);
                } else {
                    c.fillStyle = 'rgba(40,40,40,0.85)';
                    c.fillRect(rx, ry, rw, rh);
                }
                c.restore();
                return;
            }
            c.save();
            c.strokeStyle = col;
            c.lineWidth = lw;
            if (a.type === 'rect') {
                c.strokeRect(rx, ry, rw, rh);
            } else {
                c.beginPath();
                c.ellipse(rx + rw / 2, ry + rh / 2, Math.abs(rw / 2), Math.abs(rh / 2), 0, 0, Math.PI * 2);
                c.stroke();
            }
            c.restore();
            return;
        }
        if (a.type === 'arrow') {
            var s = map(a.x0, a.y0);
            var e = map(a.x1, a.y1);
            var ang = Math.atan2(e.y - s.y, e.x - s.x);
            var head = Math.max(8 * scale, lw * 3.2);
            c.save();
            c.strokeStyle = col;
            c.fillStyle = col;
            c.lineWidth = lw;
            c.lineCap = 'round';
            c.beginPath();
            c.moveTo(s.x, s.y);
            c.lineTo(e.x, e.y);
            c.stroke();
            // 箭头三角
            c.beginPath();
            c.moveTo(e.x, e.y);
            c.lineTo(e.x - head * Math.cos(ang - Math.PI / 7), e.y - head * Math.sin(ang - Math.PI / 7));
            c.lineTo(e.x - head * Math.cos(ang + Math.PI / 7), e.y - head * Math.sin(ang + Math.PI / 7));
            c.closePath();
            c.fill();
            c.restore();
            return;
        }
        if (a.type === 'text') {
            if (!a.text) return;
            var tp = map(a.x, a.y);
            var fs = (a.fontSize || 20) * scale;
            c.save();
            c.font = '600 ' + fs + 'px -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
            c.textBaseline = 'top';
            c.fillStyle = col;
            c.lineWidth = Math.max(2, fs / 8);
            c.strokeStyle = 'rgba(0,0,0,0.55)';
            c.lineJoin = 'round';
            var lines = a.text.split('\n');
            for (var li = 0; li < lines.length; li++) {
                var ly = tp.y + li * fs * 1.28;
                c.strokeText(lines[li], tp.x, ly);
                c.fillText(lines[li], tp.x, ly);
            }
            c.restore();
            return;
        }
        if (a.type === 'watermark') {
            var wtl = map(a.x, a.y);
            var wbr = map(a.x + a.w, a.y + a.h);
            var wx = wtl.x, wy = wtl.y, ww = wbr.x - wtl.x, wh = wbr.y - wtl.y;
            if (ww < 1 || wh < 1) return;
            var wtxt = a.text || tr('chat.cropWatermarkDefault', '水印');
            var wfs = (a.fontSize || 30) * scale;
            c.save();
            c.beginPath();
            c.rect(wx, wy, ww, wh);
            c.clip();
            c.translate(wx + ww / 2, wy + wh / 2);
            c.rotate(-Math.PI / 6); // 斜铺 -30°
            c.font = '600 ' + wfs + 'px -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
            c.textAlign = 'center';
            c.textBaseline = 'middle';
            c.fillStyle = a.color || currentColor;
            c.globalAlpha = (a.alpha != null) ? a.alpha : 0.26;
            var tw = Math.max(1, c.measureText(wtxt).width);
            var stepX = tw + wfs * 2.0;
            var stepY = wfs * 2.6;
            var diag = Math.sqrt(ww * ww + wh * wh);
            var half = diag / 2 + stepY;
            var row = 0;
            for (var yy = -half; yy <= half; yy += stepY) {
                // 行间错位半格，平铺更自然
                var offset = (row % 2) ? stepX / 2 : 0;
                for (var xx = -half - offset; xx <= half; xx += stepX) {
                    c.fillText(wtxt, xx, yy);
                }
                row++;
            }
            c.restore();
            return;
        }
    }

    function renderAnnotations(c, map, scale) {
        for (var i = 0; i < annotations.length; i++) {
            renderOneAnnotation(c, annotations[i], map, scale);
        }
        if (annoDraft) {
            renderOneAnnotation(c, annoDraft, map, scale);
        }
    }

    // ======================== Snapshot history (undo / redo) ========================
    // 提交一步历史：存当前 annotations 的浅拷贝。tag 用于合并连续同类编辑——传入相同 tag
    // 且仍在栈顶时，原地替换栈顶快照而非新增（水印逐字输入合成一步，不刷爆 undo）。
    function pushHistory(tag) {
        if (tag && tag === lastHistoryTag && historyIndex > 0 && historyIndex === history.length - 1) {
            history[historyIndex] = annotations.slice();
        } else {
            history.length = historyIndex + 1;     // 丢弃 redo 分支
            history.push(annotations.slice());
            historyIndex = history.length - 1;
        }
        lastHistoryTag = tag || null;
        updateUndoRedoButtons();
    }

    // 工作数组对齐到当前快照（undo/redo 与"取消编辑/无改动"都用它复原）
    function restoreFromHistory() {
        annotations = history[historyIndex].slice();
        lastHistoryTag = null; // 切快照后断开合并链，下次编辑另起一步
        syncWatermarkOptionsFromAnnotations(); // 选项条状态跟随快照，别用 undo 前的旧文字/字号
        updateUndoRedoButtons();
        requestRender();
    }

    function resetHistory() {
        annotations = [];
        history = [[]];
        historyIndex = 0;
        lastHistoryTag = null;
        updateUndoRedoButtons();
    }

    function commitAnnotation(a) {
        if (a.type === 'mosaic') {
            var n = normAnno(a);
            a._cache = buildMosaicCache(n.x, n.y, n.w, n.h, a.block);
        }
        annotations.push(a);
        pushHistory();
    }

    function undo() {
        if (textEditor) { commitTextEdit(); return; }
        if (historyIndex === 0) return;
        historyIndex--;
        restoreFromHistory();
    }

    function redo() {
        // 与 undo 一致：编辑框还开着就先收掉，别在它底下换掉 annotations ——
        // 否则提交时会把编辑后的文字插到已被快照恢复的原文字旁边，烤出重复文字。
        if (textEditor) { commitTextEdit(); return; }
        if (historyIndex >= history.length - 1) return;
        historyIndex++;
        restoreFromHistory();
    }

    function clearAnnotations() {
        annoDraft = null;
        cancelTextEdit();
        resetHistory();
    }

    function updateUndoRedoButtons() {
        if (undoBtn) undoBtn.disabled = historyIndex === 0;
        if (redoBtn) redoBtn.disabled = historyIndex >= history.length - 1;
    }

    // 把工具栏恢复到初始态（选择工具 + active 同步）
    function resetToolUI() {
        currentTool = 'select';
        if (selectionBox) selectionBox.classList.remove('crop-selection-box--drawing');
        if (toolBtns && toolBtns.select) {
            for (var k in toolBtns) {
                if (toolBtns.hasOwnProperty(k)) toolBtns[k].classList.toggle('is-active', k === 'select');
            }
        }
        syncColorWidthActive();
        updateUndoRedoButtons();
        updateOptionsBar();
    }

    function makeDraft(ip) {
        var base = { type: currentTool, color: currentColor, width: currentStrokeWidth };
        if (currentTool === 'pen' || currentTool === 'highlighter') {
            base.points = [{ x: ip.x, y: ip.y }];
        } else {
            base.x0 = ip.x; base.y0 = ip.y; base.x1 = ip.x; base.y1 = ip.y;
        }
        if (currentTool === 'highlighter') base.alpha = currentHighlightAlpha;
        if (currentTool === 'mosaic') base.block = currentMosaicBlock;
        return base;
    }

    function isDraftValid(d) {
        if (d.type === 'pen' || d.type === 'highlighter') return d.points && d.points.length >= 2;
        if (d.type === 'arrow') return Math.hypot(d.x1 - d.x0, d.y1 - d.y0) >= 5;
        var n = normAnno(d);
        return n.w >= 3 && n.h >= 3;
    }

    // ======================== Text tool ========================
    // existing：再次编辑已提交文字标注时传入（沿用其字号/颜色/文本），见 issue 文字可二次编辑。
    function beginTextEdit(pos, ip, existing) {
        commitTextEdit(); // 收掉上一个未提交的
        var scale = displayScale() || 1;
        var fontSizeImage = existing
            ? (existing.fontSize || Math.max(8, Math.round(currentFontSizePx / scale)))
            : Math.max(8, Math.round(currentFontSizePx / scale));
        var col = existing ? (existing.color || currentColor) : currentColor;
        var ta = document.createElement('textarea');
        ta.className = 'crop-text-editor';
        ta.rows = 1;
        ta.wrap = 'off';
        ta.style.left = pos.x + 'px';
        ta.style.top = pos.y + 'px';
        ta.style.color = col;
        ta.style.fontSize = Math.round(fontSizeImage * scale) + 'px';
        ta._imgX = ip.x; ta._imgY = ip.y; ta._fontSizeImage = fontSizeImage; ta._color = col;
        ta._original = existing || null; // 二次编辑时记下原标注，Esc 取消可原样还原
        function autosize() {
            ta.style.height = 'auto';
            ta.style.height = ta.scrollHeight + 'px';
            ta.style.width = 'auto';
            ta.style.width = Math.min(workspaceEl.clientWidth - pos.x - 12, Math.max(40, ta.scrollWidth + 4)) + 'px';
        }
        ta.addEventListener('keydown', function (e) {
            e.stopPropagation(); // 别让遮罩 keydown 抢走 Esc/Enter/Delete/方向键
            if (e.key === 'Escape') { e.preventDefault(); cancelTextEdit(true); }
            else if ((e.key === 'Enter' || e.key === 'NumpadEnter') && !e.shiftKey) { e.preventDefault(); commitTextEdit(); }
        });
        ta.addEventListener('blur', function (e) {
            // 焦点移到"样式控件"（选项条字号滑块、调色板、线宽）时不要提交——否则刚点一下
            // textarea 就 blur 提交、编辑器被移除，setColor/字号回调拿到的 textEditor 已是 null，
            // 改动只作用于后续文字而非当前正在编辑的标注（Codex P2）。切工具/确认等会另行提交。
            var next = e.relatedTarget || document.activeElement;
            if (next && (
                (optionsBarEl && optionsBarEl.contains(next)) ||
                (next.classList && (next.classList.contains('crop-color-swatch')
                    || next.classList.contains('crop-width-btn')))
            )) return;
            commitTextEdit();
        });
        ta.addEventListener('input', autosize);
        workspaceEl.appendChild(ta);
        textEditor = ta;
        if (existing && existing.text) {
            ta.value = existing.text;
            autosize();
        }
        // 异步聚焦，避免 mousedown 同帧抢焦点失败
        setTimeout(function () { try { ta.focus(); ta.select(); } catch (e) {} }, 0);
    }

    // 估算文字标注在图片自然坐标下的包围盒（用于"点中已有文字 → 二次编辑"命中测试）
    function measureTextAnno(a) {
        var fs = a.fontSize || 20;
        var lines = (a.text || '').split('\n');
        var maxw = 0;
        ctx.save();
        ctx.font = '600 ' + fs + 'px -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif';
        for (var i = 0; i < lines.length; i++) {
            maxw = Math.max(maxw, ctx.measureText(lines[i]).width);
        }
        ctx.restore();
        return { x: a.x, y: a.y, w: maxw, h: Math.max(1, lines.length) * fs * 1.28 };
    }

    // 返回命中的文字标注下标（从最上层往下找），无命中返回 -1。ix/iy 为图片自然坐标。
    function hitTestText(ix, iy) {
        for (var i = annotations.length - 1; i >= 0; i--) {
            var a = annotations[i];
            if (a.type !== 'text') continue;
            var b = measureTextAnno(a);
            var pad = Math.max(4, (a.fontSize || 20) * 0.35);
            if (ix >= b.x - pad && ix <= b.x + b.w + pad &&
                iy >= b.y - pad && iy <= b.y + b.h + pad) {
                return i;
            }
        }
        return -1;
    }

    // 把已提交的文字标注重新拉回编辑框：从工作数组摘掉（编辑期间不重复绘制），
    // 但不动历史快照——当前快照仍持有它，取消/无改动时 restoreFromHistory 即可原样复原。
    function reopenTextAnnotation(idx) {
        var a = annotations[idx];
        annotations.splice(idx, 1); // 仅改工作数组；快照是独立副本，不受影响
        requestRender();
        // 同步字号滑块到被编辑文字的真实字号（存的是图片坐标，换算回屏幕 px 并夹到滑块范围），
        // 否则滑块还显示默认值，轻碰一下就把这段文字跳到默认字号。
        var scale = displayScale() || 1;
        currentFontSizePx = Math.max(12, Math.min(80, Math.round((a.fontSize || 20) * scale)));
        updateOptionsBar();
        var posC = imageToCanvas(a.x, a.y);
        beginTextEdit(posC, { x: a.x, y: a.y }, a);
        // 记下原层级：真改动提交时插回原位，保住层级。
        if (textEditor) textEditor._originalIndex = idx;
    }

    function commitTextEdit() {
        if (!textEditor) return;
        var ta = textEditor;
        textEditor = null; // 先置空，规避 removeChild 触发 blur 的重入
        var text = ta.value.replace(/[\s]+$/, '');
        if (ta.parentNode) ta.parentNode.removeChild(ta);
        if (text) {
            var anno = {
                type: 'text', text: text,
                x: ta._imgX, y: ta._imgY,
                fontSize: ta._fontSizeImage, color: ta._color
            };
            // 二次编辑若文字/字号/颜色都没变（位置二次编辑不动）= no-op：丢弃新对象，
            // 工作数组回到当前快照（含原对象、原层级），不新增历史步。
            var unchanged = ta._original
                && ta._original.text === anno.text
                && ta._original.fontSize === anno.fontSize
                && ta._original.color === anno.color;
            if (unchanged) {
                restoreFromHistory();
                return;
            }
            var idx = (ta._originalIndex != null)
                ? Math.max(0, Math.min(annotations.length, ta._originalIndex))
                : annotations.length;
            annotations.splice(idx, 0, anno);
            pushHistory(); // 新建 / 真改动 → 独立历史步
        } else if (ta._original) {
            // 二次编辑删空 = 删除该标注（工作数组已无它），记一步删除历史。
            pushHistory();
        }
        requestRender();
    }

    // restoreOriginal=true 仅用于"按 Esc 取消二次编辑"：工作数组回到当前快照（原对象、原层级、redo 分支都保留）。
    // clearAnnotations / close 等全局清空路径传 false（不复原，随后 resetHistory 整体清空）。
    function cancelTextEdit(restoreOriginal) {
        if (!textEditor) return;
        var ta = textEditor;
        textEditor = null;
        if (ta.parentNode) ta.parentNode.removeChild(ta);
        if (restoreOriginal && ta._original) restoreFromHistory();
        else requestRender();
    }

    // ======================== Toolbar ========================
    var ICONS = {
        select: '<path d="M5 3l13 6.4-5.4 1.6L9.6 18z"/>',
        rect: '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
        ellipse: '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
        arrow: '<path d="M5 19L18 6"/><path d="M11.5 6H18v6.5"/>',
        pen: '<path d="M4 20l1.2-4L16 5.2l2.8 2.8L8 18.8z"/><path d="M14 7.2l2.8 2.8"/>',
        highlighter: '<path d="M3 20h6"/><path d="M10.5 15.5l-3 3 1.5 1.5 3-1 7.5-7.5-2.5-2.5z"/><path d="M14.5 6.5l3 3"/>',
        text: '<path d="M6 19l6-14 6 14"/><path d="M8.8 13.5h6.4"/>',
        mosaic: '<rect x="5" y="5" width="4" height="4"/><rect x="13" y="5" width="4" height="4"/><rect x="9" y="9" width="4" height="4"/><rect x="5" y="13" width="4" height="4"/><rect x="13" y="13" width="4" height="4"/>',
        watermark: '<path d="M4 18l4-12 4 12"/><path d="M14 18l4-12 4 12"/>',
        undo: '<path d="M9 8L4 13l5 5"/><path d="M4 13h9.5a5.5 5.5 0 0 1 0 11H11"/>',
        redo: '<path d="M15 8l5 5-5 5"/><path d="M20 13h-9.5a5.5 5.5 0 0 0 0 11H13"/>',
        save: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
        confirm: '<path d="M5 13l4.5 4.5L19 7"/>',
        cancel: '<path d="M6 6l12 12M18 6L6 18"/>'
    };

    function svgIcon(name) {
        var filled = (name === 'select' || name === 'mosaic');
        var fill = filled ? 'currentColor' : 'none';
        var stroke = filled ? 'none' : 'currentColor';
        return '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="' +
            fill + '" stroke="' + stroke + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            (ICONS[name] || '') + '</svg>';
    }

    function divider() {
        var d = document.createElement('span');
        d.className = 'crop-tool-divider';
        d.setAttribute('aria-hidden', 'true');
        return d;
    }

    function makeToolButton(cls, html, titleKey, fb, onClick) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = cls;
        b.innerHTML = html;
        b.title = tr(titleKey, fb);
        b.setAttribute('aria-label', b.title);
        b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', onClick);
        return b;
    }

    function syncColorWidthActive() {
        // 水印工具下调色板反映/控制水印自己的颜色，其它工具用全局绘图色
        var activeColor = (currentTool === 'watermark') ? currentWatermarkColor : currentColor;
        for (var i = 0; i < colorSwatches.length; i++) {
            var cOn = colorSwatches[i]._color === activeColor;
            colorSwatches[i].classList.toggle('is-active', cOn);
            colorSwatches[i].setAttribute('aria-pressed', cOn ? 'true' : 'false');
        }
        for (var j = 0; j < widthBtns.length; j++) {
            var wOn = widthBtns[j]._width === currentStrokeWidth;
            widthBtns[j].classList.toggle('is-active', wOn);
            widthBtns[j].setAttribute('aria-pressed', wOn ? 'true' : 'false');
        }
    }

    function ensureToolbar() {
        if (toolbarEl) return;
        var bar = document.createElement('div');
        bar.className = 'crop-toolbar';
        bar.style.display = 'none';
        // 工具栏上的 mousedown 不冒泡到 document，免得被当成绘制/选区操作
        bar.addEventListener('mousedown', function (e) { e.stopPropagation(); });
        bar.addEventListener('touchstart', function (e) { e.stopPropagation(); }, { passive: true });

        var TOOLS = [
            ['select', 'chat.cropToolSelect', '选择/移动'],
            ['rect', 'chat.cropToolRect', '矩形'],
            ['ellipse', 'chat.cropToolEllipse', '椭圆'],
            ['arrow', 'chat.cropToolArrow', '箭头'],
            ['pen', 'chat.cropToolPen', '画笔'],
            ['highlighter', 'chat.cropToolHighlight', '荧光笔'],
            ['text', 'chat.cropToolText', '文字'],
            ['mosaic', 'chat.cropToolMosaic', '马赛克'],
            ['watermark', 'chat.cropToolWatermark', '水印']
        ];
        var toolGrp = document.createElement('div');
        toolGrp.className = 'crop-tool-group';
        TOOLS.forEach(function (t) {
            var b = makeToolButton('crop-tool-btn', svgIcon(t[0]), t[1], t[2], function () { setTool(t[0]); });
            toolBtns[t[0]] = b;
            toolGrp.appendChild(b);
        });
        bar.appendChild(toolGrp);
        bar.appendChild(divider());

        // 调色板
        var colGrp = document.createElement('div');
        colGrp.className = 'crop-tool-group crop-color-group';
        PALETTE.forEach(function (col, idx) {
            var sw = document.createElement('button');
            sw.type = 'button';
            sw.className = 'crop-color-swatch';
            sw.style.background = col;
            sw._color = col;
            var lbl = COLOR_LABELS[idx] ? tr(COLOR_LABELS[idx][0], COLOR_LABELS[idx][1]) : col;
            sw.title = lbl;
            sw.setAttribute('aria-label', lbl);
            sw.setAttribute('aria-pressed', 'false');
            sw.addEventListener('click', function () { setColor(col); });
            colorSwatches.push(sw);
            colGrp.appendChild(sw);
        });
        bar.appendChild(colGrp);

        // 线宽 S/M/L
        var wGrp = document.createElement('div');
        wGrp.className = 'crop-tool-group crop-width-group';
        var WLABEL = ['S', 'M', 'L'];
        WIDTH_PRESETS.forEach(function (w, i) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'crop-width-btn';
            b.textContent = WLABEL[i];
            b._width = w;
            b.setAttribute('aria-pressed', 'false');
            b.addEventListener('click', function () { setWidth(w); });
            widthBtns.push(b);
            wGrp.appendChild(b);
        });
        bar.appendChild(wGrp);
        bar.appendChild(divider());

        // 撤销 / 重做 / 保存
        var actGrp = document.createElement('div');
        actGrp.className = 'crop-tool-group';
        undoBtn = makeToolButton('crop-tool-btn', svgIcon('undo'), 'chat.cropUndo', '撤销', undo);
        redoBtn = makeToolButton('crop-tool-btn', svgIcon('redo'), 'chat.cropRedo', '重做', redo);
        var saveBtn = makeToolButton('crop-tool-btn', svgIcon('save'), 'chat.cropSave', '保存到文件', saveToFile);
        actGrp.appendChild(undoBtn);
        actGrp.appendChild(redoBtn);
        actGrp.appendChild(saveBtn);
        bar.appendChild(actGrp);
        bar.appendChild(divider());

        // 取消选区 / 贴图 / 确认
        var endGrp = document.createElement('div');
        endGrp.className = 'crop-tool-group';
        var cancelBtn = makeToolButton('crop-tool-btn crop-tool-cancel', svgIcon('cancel'), 'chat.cropClearSelectionTitle', '取消选区', clearSelection);
        pinBtn = document.createElement('button');
        pinBtn.type = 'button';
        pinBtn.className = 'crop-tool-btn crop-tool-pin';
        pinBtn.textContent = tr('chat.cropPin', '贴图');
        pinBtn.title = tr('chat.cropPinTitle', '将截图贴到桌面');
        pinBtn.setAttribute('aria-label', pinBtn.title);
        pinBtn.style.display = 'none';
        pinBtn.addEventListener('click', pinCrop);
        var confirmBtn = makeToolButton('crop-tool-btn crop-tool-confirm', svgIcon('confirm'), 'chat.cropConfirmTitle', '确认截图', confirmCrop);
        endGrp.appendChild(cancelBtn);
        endGrp.appendChild(pinBtn);
        endGrp.appendChild(confirmBtn);
        bar.appendChild(endGrp);

        toolbarEl = bar;
        toolBtns.select.classList.add('is-active');
        syncColorWidthActive();
        updateUndoRedoButtons();
    }

    function setTool(name) {
        if (name !== 'text') commitTextEdit(); // 切走文字工具先提交
        currentTool = name;
        for (var k in toolBtns) {
            if (toolBtns.hasOwnProperty(k)) {
                var on = (k === name);
                toolBtns[k].classList.toggle('is-active', on);
                toolBtns[k].setAttribute('aria-pressed', on ? 'true' : 'false');
            }
        }
        // 绘图工具下隐藏选区手柄/网格，避免误拖
        if (selectionBox) selectionBox.classList.toggle('crop-selection-box--drawing', !!DRAW_TOOLS[name]);
        if (canvas) canvas.style.cursor = (name === 'text') ? 'text' : 'crosshair';
        // 选水印工具时，若选区已存在且尚无水印，自动铺一层（点击/改选项可再刷新）
        if (name === 'watermark' && sel) ensureWatermark();
        syncColorWidthActive(); // 调色板高亮跟随工具（水印有独立颜色）
        updateOptionsBar();
        requestRender();
    }

    function setColor(col) {
        // 水印工具：改的是水印独立颜色，不碰全局绘图色 currentColor
        if (currentTool === 'watermark') {
            currentWatermarkColor = col;
            syncColorWidthActive();
            updateActiveWatermark();
            return;
        }
        currentColor = col;
        syncColorWidthActive();
        if (textEditor) { textEditor.style.color = col; textEditor._color = col; }
    }

    function setWidth(w) {
        currentStrokeWidth = w;
        syncColorWidthActive();
    }

    var FLOATING_PANEL_MARGIN = 12;
    var FLOATING_PANEL_GAP = 8;

    function getOverlayClientSize() {
        return {
            w: (overlay && overlay.clientWidth) || window.innerWidth || document.documentElement.clientWidth || 0,
            h: (overlay && overlay.clientHeight) || window.innerHeight || document.documentElement.clientHeight || 0
        };
    }

    function clampPanelCoord(value, panelSize, viewportSize) {
        var min = FLOATING_PANEL_MARGIN;
        var max = viewportSize - panelSize - FLOATING_PANEL_MARGIN;
        if (max < min) return min;
        return Math.max(min, Math.min(max, value));
    }

    function prepareFloatingPanel(panel) {
        var size = getOverlayClientSize();
        panel.style.maxWidth = Math.max(1, size.w - FLOATING_PANEL_MARGIN * 2) + 'px';
        // 先恢复内容自身宽度再测量，避免上一次 left 位置影响 absolute auto-width 的换行结果。
        panel.style.width = 'max-content';
    }

    function measureFloatingPanel(panel, fallbackW, fallbackH) {
        var rect = panel.getBoundingClientRect ? panel.getBoundingClientRect() : null;
        var overlaySize = getOverlayClientSize();
        var maxW = Math.max(1, overlaySize.w - FLOATING_PANEL_MARGIN * 2);
        var w = Math.max(
            fallbackW || 0,
            panel.offsetWidth || 0,
            rect ? rect.width : 0
        );
        var h = Math.max(
            fallbackH || 0,
            panel.offsetHeight || 0,
            rect ? rect.height : 0,
            panel.scrollHeight || 0
        );
        if (panel.scrollWidth) w = Math.max(w, Math.min(panel.scrollWidth, maxW));
        return {
            w: Math.ceil(Math.min(w, maxW)),
            h: Math.ceil(h)
        };
    }

    function pickPanelLeft(anchorX, anchorW, panelW) {
        var size = getOverlayClientSize();
        var min = FLOATING_PANEL_MARGIN;
        var max = size.w - panelW - FLOATING_PANEL_MARGIN;
        var candidates = [
            anchorX,
            anchorX + anchorW - panelW,
            anchorX + (anchorW - panelW) / 2
        ];
        for (var i = 0; i < candidates.length; i++) {
            if (candidates[i] >= min && candidates[i] <= max) return candidates[i];
        }
        return clampPanelCoord(anchorX, panelW, size.w);
    }

    function pickPanelTop(anchorY, anchorH, panelH) {
        var size = getOverlayClientSize();
        var below = anchorY + anchorH + FLOATING_PANEL_GAP;
        var above = anchorY - panelH - FLOATING_PANEL_GAP;
        var spaceBelow = size.h - FLOATING_PANEL_MARGIN - below - panelH;
        var spaceAbove = anchorY - FLOATING_PANEL_MARGIN - FLOATING_PANEL_GAP;
        var belowFits = spaceBelow >= 0;
        var aboveFits = above >= FLOATING_PANEL_MARGIN;
        var anchorCenterY = anchorY + anchorH / 2;

        // 底部区域优先上翻，避免工具栏跟着选区向下拖动时被屏幕边缘吃掉。
        if (aboveFits && (!belowFits || anchorCenterY >= size.h / 2 || spaceAbove > spaceBelow)) {
            return above;
        }
        if (belowFits) return below;
        if (aboveFits) return above;
        return clampPanelCoord(spaceAbove > spaceBelow ? above : below, panelH, size.h);
    }

    function placeFloatingPanel(panel, anchor, fallbackW, fallbackH) {
        prepareFloatingPanel(panel);
        var panelSize = measureFloatingPanel(panel, fallbackW, fallbackH);
        panel.style.width = panelSize.w + 'px';
        var left = pickPanelLeft(anchor.x, anchor.w, panelSize.w);
        var top = pickPanelTop(anchor.y, anchor.h, panelSize.h);
        panel.style.left = Math.round(left) + 'px';
        panel.style.top = Math.round(top) + 'px';
        panelSize = measureFloatingPanel(panel, panelSize.w, panelSize.h);
        left = clampPanelCoord(left, panelSize.w, getOverlayClientSize().w);
        top = clampPanelCoord(top, panelSize.h, getOverlayClientSize().h);
        panel.style.left = Math.round(left) + 'px';
        panel.style.top = Math.round(top) + 'px';
        return {
            x: left,
            y: top,
            w: panelSize.w,
            h: panelSize.h
        };
    }

    function positionToolbar(cs) {
        if (!toolbarEl) return;
        toolbarEl.style.display = 'flex';
        var placedToolbar = placeFloatingPanel(toolbarEl, cs, 360, 44);
        positionOptionsBar(placedToolbar);
    }

    // 选项条贴着主工具栏：默认浮在工具栏下方（仿 QQ），下方放不下则翻到上方（随屏幕边缘自适应）。
    function positionOptionsBar(toolbarRect) {
        if (!optionsBarEl || optionsBarEl.style.display === 'none') return;
        placeFloatingPanel(optionsBarEl, toolbarRect, 240, 40);
    }

    // ======================== Contextual options bar ========================
    function ensureOptionsBar() {
        if (optionsBarEl) return;
        var bar = document.createElement('div');
        bar.className = 'crop-options-bar';
        bar.style.display = 'none';
        bar.addEventListener('mousedown', function (e) { e.stopPropagation(); });
        bar.addEventListener('touchstart', function (e) { e.stopPropagation(); }, { passive: true });
        optionsBarEl = bar;
    }

    // 标签 + 滑块 + 数值的一行
    function makeSlider(labelKey, fb, min, max, value, onInput) {
        var row = document.createElement('label');
        row.className = 'crop-opt-row';
        var label = document.createElement('span');
        label.className = 'crop-opt-label';
        label.textContent = tr(labelKey, fb);
        var input = document.createElement('input');
        input.type = 'range';
        input.className = 'crop-opt-slider';
        input.min = min; input.max = max; input.value = value;
        var val = document.createElement('span');
        val.className = 'crop-opt-value';
        val.textContent = value;
        input.addEventListener('input', function () {
            val.textContent = input.value;
            onInput(Number(input.value));
        });
        row.appendChild(label);
        row.appendChild(input);
        row.appendChild(val);
        return row;
    }

    // 按当前工具重建选项条内容；select / 形状 / 箭头 / 画笔 无独立选项则隐藏。
    function updateOptionsBar() {
        if (!optionsBarEl) return;
        if (!sel || !OPTION_TOOLS[currentTool]) {
            optionsBarEl.style.display = 'none';
            return;
        }
        optionsBarEl.innerHTML = '';
        if (currentTool === 'text') {
            optionsBarEl.appendChild(makeSlider('chat.cropFontSize', '字号', 12, 80, currentFontSizePx, function (v) {
                currentFontSizePx = v;
                if (textEditor) {
                    var scale = displayScale() || 1;
                    var fsi = Math.max(8, Math.round(v / scale));
                    textEditor._fontSizeImage = fsi;
                    textEditor.style.fontSize = Math.round(fsi * scale) + 'px';
                }
            }));
        } else if (currentTool === 'highlighter') {
            optionsBarEl.appendChild(makeSlider('chat.cropOpacity', '透明度', 10, 90, Math.round(currentHighlightAlpha * 100), function (v) {
                currentHighlightAlpha = v / 100;
            }));
        } else if (currentTool === 'mosaic') {
            optionsBarEl.appendChild(makeSlider('chat.cropMosaicSize', '颗粒', 4, 40, currentMosaicBlock, function (v) {
                currentMosaicBlock = v;
            }));
        } else if (currentTool === 'watermark') {
            var row = document.createElement('label');
            row.className = 'crop-opt-row';
            var label = document.createElement('span');
            label.className = 'crop-opt-label';
            label.textContent = tr('chat.cropWatermarkText', '文字');
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'crop-opt-text';
            input.value = currentWatermarkText;
            input.placeholder = tr('chat.cropWatermarkDefault', '水印');
            input.addEventListener('input', function () {
                currentWatermarkText = input.value;
                updateActiveWatermark();
            });
            // 输入框内的按键不冒泡到遮罩，避免被当成快捷键
            input.addEventListener('keydown', function (e) { e.stopPropagation(); });
            row.appendChild(label);
            row.appendChild(input);
            optionsBarEl.appendChild(row);
            optionsBarEl.appendChild(makeSlider('chat.cropFontSize', '字号', 16, 96, currentWatermarkSizePx, function (v) {
                currentWatermarkSizePx = v;
                updateActiveWatermark();
            }));
        }
        optionsBarEl.style.display = 'flex';
        if (sel) {
            var cs = clampSel(sel);
            if (cs) positionToolbar(cs);
        }
    }

    // ======================== Watermark helpers ========================
    function findWatermarkIndex() {
        for (var i = annotations.length - 1; i >= 0; i--) {
            if (annotations[i].type === 'watermark') return i;
        }
        return -1;
    }

    // 按当前选区铺/刷新一层斜铺文字水印。水印对象始终"替换不原地改"，保住快照不可变。
    // 历史用 'watermark' tag 合并：连续的水印改动（覆盖/逐字输入）合成一步 undo。
    function ensureWatermark() {
        var cs = clampSel(sel);
        if (!cs) return;
        var p1 = canvasToImage(cs.x, cs.y);
        var p2 = canvasToImage(cs.x + cs.w, cs.y + cs.h);
        var rect = {
            x: Math.min(p1.x, p2.x), y: Math.min(p1.y, p2.y),
            w: Math.abs(p2.x - p1.x), h: Math.abs(p2.y - p1.y)
        };
        var scale = displayScale() || 1;
        var fontImg = Math.max(12, Math.round(currentWatermarkSizePx / scale));
        var anno = {
            type: 'watermark', x: rect.x, y: rect.y, w: rect.w, h: rect.h,
            text: currentWatermarkText || tr('chat.cropWatermarkDefault', '水印'),
            color: currentWatermarkColor, fontSize: fontImg, alpha: 0.26
        };
        var idx = findWatermarkIndex();
        if (idx >= 0) {
            var old = annotations[idx];
            if (old.text === anno.text && old.color === anno.color && old.fontSize === anno.fontSize
                && old.x === anno.x && old.y === anno.y && old.w === anno.w && old.h === anno.h) {
                return; // 无变化（如重复点工具），不记历史
            }
            annotations = annotations.slice();
            annotations[idx] = anno; // 替换而非原地改
        } else {
            annotations = annotations.concat([anno]);
        }
        pushHistory('watermark');
        requestRender();
    }

    // undo/redo 切快照后，把选项条状态（水印文字/字号）对齐到恢复出来的水印标注。
    // 必须无条件同步 current* 变量（即使当前不是水印工具）—— 否则"改水印→切到别的工具→undo"
    // 时 current* 仍是旧值，等切回水印工具时 setTool 自动 ensureWatermark 会用旧文字重建、
    // 把撤销掉的文字又贴回来（Codex P2）。选项条 DOM 只在水印工具激活时才需要重建。
    function syncWatermarkOptionsFromAnnotations() {
        var idx = findWatermarkIndex();
        if (idx >= 0) {
            var wm = annotations[idx];
            currentWatermarkText = wm.text || '';
            var scale = displayScale() || 1;
            currentWatermarkSizePx = Math.max(16, Math.min(96, Math.round((wm.fontSize || 30) * scale)));
            // 同步水印自己的颜色（不碰全局绘图色 currentColor，免得 undo 把 pen/箭头的颜色改掉）。
            if (wm.color) currentWatermarkColor = wm.color;
        }
        if (currentTool === 'watermark') { syncColorWidthActive(); updateOptionsBar(); }
    }

    // 选项/调色板变动时刷新已铺的水印（替换对象）。逐字输入经 'watermark' tag 合并成一步 undo。
    function updateActiveWatermark() {
        var idx = findWatermarkIndex();
        if (idx < 0) return;
        var scale = displayScale() || 1;
        var old = annotations[idx];
        annotations = annotations.slice();
        annotations[idx] = {
            type: 'watermark', x: old.x, y: old.y, w: old.w, h: old.h,
            text: currentWatermarkText || tr('chat.cropWatermarkDefault', '水印'),
            color: currentWatermarkColor,
            fontSize: Math.max(12, Math.round(currentWatermarkSizePx / scale)),
            alpha: old.alpha
        };
        pushHistory('watermark');
        requestRender();
    }

    function saveToFile() {
        var url = cropToDataUrl(); // 原清晰度 PNG（含标注）
        if (!url) return;
        try {
            var a = document.createElement('a');
            a.href = url;
            a.download = 'neko-screenshot-' + Date.now() + '.png';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (err) {
            console.warn('[crop] save to file failed:', err);
        }
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
            // 未选区时按原图亮度预览：只把图片区域上的暗罩清掉，不再额外压暗。
            // 之前这里又盖了一层 rgba(0,0,0,0.15)，让刚进编辑界面的整图就暗了 15%、
            // 对比度也被拉低显得发糊——用户要的是临时预览＝原图画质，故移除。
            // 图片外的 letterbox 仍保留上面的 0.5 遮罩。
            ctx.clearRect(imgDisplayLeft, imgDisplayTop, imgDisplayWidth, imgDisplayHeight);
            return;
        }

        var cs = clampSel(sel);
        if (!cs) return;

        // Clear selected region
        ctx.clearRect(cs.x, cs.y, cs.w, cs.h);

        // Annotations — clipped to the selection so nothing bleeds into the mask
        if (annotations.length || annoDraft) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(cs.x, cs.y, cs.w, cs.h);
            ctx.clip();
            renderAnnotations(ctx, imageToCanvas, displayScale());
            ctx.restore();
        }

        // Border
        ctx.strokeStyle = '#44b7fe';
        ctx.lineWidth = 2;
        ctx.setLineDash([8, 4]);
        ctx.strokeRect(cs.x, cs.y, cs.w, cs.h);
        ctx.setLineDash([]);
    }

    // ======================== Action buttons position ========================
    function updateSelectionUI() {
        if (!selectionBox || !selectionBadge || !toolbarEl) return;
        if (!sel) {
            selectionBox.style.display = 'none';
            selectionBadge.style.display = 'none';
            toolbarEl.style.display = 'none';
            if (optionsBarEl) optionsBarEl.style.display = 'none';
            return;
        }
        var cs = clampSel(sel);
        if (!cs) {
            selectionBox.style.display = 'none';
            selectionBadge.style.display = 'none';
            toolbarEl.style.display = 'none';
            if (optionsBarEl) optionsBarEl.style.display = 'none';
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

        positionToolbar(cs);
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

        // 0. 绘图工具优先：必须在选区内起笔，完全跳过选区手柄/移动逻辑。
        //    选区外起笔只会画出被 clip 掉的隐形标注、污染撤销栈，故直接忽略。
        if (DRAW_TOOLS[currentTool]) {
            if (!sel || !hitTestInside(pos.x, pos.y)) return;
            var cp0 = clampPointToSel(pos.x, pos.y);
            var ip0 = canvasToImage(cp0.x, cp0.y);
            if (currentTool === 'text') {
                // 先把仍开着的编辑框收掉，让 annotations 落定再做命中测试 —— 否则连续点第二段
                // 文字时，beginTextEdit 内部的延迟提交会在 splice 之后改变数组，_originalIndex
                // 失配、两段文字层级互换。
                commitTextEdit();
                // 命中已有文字则拉回二次编辑，否则新建（issue 3）
                var hitIdx = hitTestText(ip0.x, ip0.y);
                if (hitIdx >= 0) reopenTextAnnotation(hitIdx);
                else beginTextEdit(cp0, ip0);
                return;
            }
            if (currentTool === 'watermark') {
                // 水印覆盖整个选区，点击即按当前选区刷新一次（不走拖拽草稿）
                ensureWatermark();
                return;
            }
            annoDraft = makeDraft(ip0);
            requestRender();
            return;
        }

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

        // 绘图草稿进行中：累积点 / 更新终点（clamp 到选区，画到边缘即止）
        if (annoDraft) {
            e.preventDefault();
            var cp = clampPointToSel(pos.x, pos.y);
            var ip = canvasToImage(cp.x, cp.y);
            if (annoDraft.points) {
                annoDraft.points.push({ x: ip.x, y: ip.y });
            } else {
                annoDraft.x1 = ip.x; annoDraft.y1 = ip.y;
            }
            requestRender();
            return;
        }

        if (mode === MODE_NONE) {
            // 绘图工具：光标固定为十字 / 文字
            if (DRAW_TOOLS[currentTool]) {
                canvas.style.cursor = currentTool === 'text' ? 'text' : 'crosshair';
                requestRender();
                return;
            }
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
        // 绘图草稿收尾（独立于选区状态机）
        if (annoDraft) {
            var d = annoDraft;
            annoDraft = null;
            if (isDraftValid(d)) commitAnnotation(d);
            requestRender();
            return;
        }
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
        if (currentTool !== 'select') return; // 绘图工具下双击不触发确认
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
        if (toolbarEl) toolbarEl.style.display = 'none';
        if (optionsBarEl) optionsBarEl.style.display = 'none';
    }

    function clearSelection() {
        sel = null;
        mode = MODE_NONE;
        clearAnnotations();   // 选区即标注画布；清选区连带清标注
        // 复位到选择工具：否则清选区后工具栏已隐藏，绘图工具仍激活，
        // 用户再拖拽会被绘图分支拦截画成隐形标注，无法重新框选。
        resetToolUI();
        hideActionBtns();
        requestRender();
    }

    function cropToDataUrl() {
        // 先收掉仍开着的文字编辑框：用户改完字号（焦点停在选项条滑块上、textarea 已 blur
        // 但未提交）后直接点确认/保存时，工具栏不会再触发 textarea blur，不先提交就烤制
        // 会丢掉这段可见文字（Codex P2）。commitTextEdit 无开启的编辑框时是 no-op。
        commitTextEdit();
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

        // 烤进标注：与实时预览共用 renderAnnotations，映射为"减去裁剪原点"，
        // scale=1（tmp canvas 已是自然分辨率）。clip 到裁剪框保持与预览一致。
        if (annotations.length) {
            tmpCtx.save();
            tmpCtx.beginPath();
            tmpCtx.rect(0, 0, cw, ch);
            tmpCtx.clip();
            renderAnnotations(tmpCtx, function (ix, iy) {
                return { x: ix - cx, y: iy - cy };
            }, 1);
            tmpCtx.restore();
        }
        // 原清晰度 PNG（不压缩）。剪贴板/保存都用它；发猫娘的 720p 压缩在 app-buttons 里做。
        return tmpCanvas.toDataURL('image/png');
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

    function pinCrop() {
        var result = cropToDataUrl();
        if (!result) return;
        close({ action: 'pin', dataUrl: result });
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
        clearAnnotations();   // 清标注 + 关文字编辑框 + 重置撤销栈
        resetToolUI();
        sourceDataUrl = null;
        originalDataUrl = null;
        recaptureFn = null;
        onRecaptureImageReady = null;
        rightClickCancelsAll = false;
        allowPin = false;
        if (pinBtn) pinBtn.style.display = 'none';
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
        commitTextEdit();      // 文字编辑框按旧布局定位，resize 前先收掉
        // 先用旧 metrics 把选区换算成图片坐标——computeImgMetrics 会改 imgDisplay*，
        // 直接 clampSel 旧的 canvas 坐标 sel 不等于同一片图片像素，会让选区相对截图漂移。
        var selImg = null;
        if (sel) {
            var p1 = canvasToImage(sel.x, sel.y);
            var p2 = canvasToImage(sel.x + sel.w, sel.y + sel.h);
            selImg = { x0: p1.x, y0: p1.y, x1: p2.x, y1: p2.y };
        }
        sizeCanvas();
        computeImgMetrics();
        // 再用新 metrics 映射回 canvas 坐标。标注本就是图片坐标，天然存活。
        if (selImg) {
            var q1 = imageToCanvas(selImg.x0, selImg.y0);
            var q2 = imageToCanvas(selImg.x1, selImg.y1);
            sel = clampSel({ x: q1.x, y: q1.y, w: q2.x - q1.x, h: q2.y - q1.y });
        }
        requestRender();
    }

    function sizeCanvas() {
        canvas.width = overlay.clientWidth;
        canvas.height = overlay.clientHeight;
    }

    // ======================== Image loading ========================
    function loadImage(dataUrl, onLoaded) {
        imgEl.onload = function () {
            sizeCanvas();
            computeImgMetrics();
            sel = null;
            clearAnnotations();   // 换了底图，旧标注无意义
            hideActionBtns();
            requestRender();
            overlay.focus();
            if (typeof onLoaded === 'function') {
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () { onLoaded(); });
                });
            }
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
            onRecaptureImageReady = (opts && opts.onRecaptureImageReady) || null;
            rightClickCancelsAll = !!(opts && opts.rightClickCancelsAll);
            allowPin = !!(opts && opts.allowPin);

            // Reset state
            sel = null;
            mode = MODE_NONE;
            clearAnnotations();
            resetToolUI();
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
            if (pinBtn) {
                pinBtn.textContent = tr('chat.cropPin', '\u8d34\u56fe');
                pinBtn.title = tr('chat.cropPinTitle', '\u5c06\u622a\u56fe\u8d34\u5230\u684c\u9762');
                pinBtn.setAttribute('aria-label', pinBtn.title);
                pinBtn.style.display = allowPin ? '' : 'none';
            }
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
