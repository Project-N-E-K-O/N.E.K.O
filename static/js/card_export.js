/**
 * card_export.js – 角色卡导出页面交互逻辑
 *
 * 功能：
 *  1. 获取角色列表
 *  2. 加载选中角色的模型（Live2D / VRM / MMD）到隐藏渲染层
 *  3. 持续从模型画布截屏到卡片预览区（实时所见即所得）
 *  4. 支持拖拽偏移 / 滚轮缩放调整构图
 *  5. 导出完整角色卡或仅导出设定
 */
(function () {
    'use strict';

    // ====== 状态 ======
    let currentCharaName = '';
    let currentModelType = '';   // 'live2d' | 'vrm' | 'mmd'
    let isModelLoaded = false;
    let previewLoopId = null;     // requestAnimationFrame ID
    let lastPreviewTime = 0;      // 上次预览渲染时间戳

    // 构图参数
    const composition = { offsetX: 0, offsetY: 0, scale: 100, rotation: 0 };

    // 贴纸状态
    const stickers = [];           // { id, src, x, y, w, h, rotation, layer, imgEl }
    let stickerIdCounter = 0;
    let selectedStickerId = null;

    // 可用贴纸列表
    const STICKER_FILES = [
        'add.png', 'angry_cat.png', 'calm_cat.png', 'cat_icon.png',
        'character_icon.png', 'chat_bubble.png', 'chat_icon.png',
        'default_character_card.png', 'emotion_model_icon.png',
        'exclamation.png', 'happy_cat.png', 'icon_systray.ico',
        'paw_ui.png', 'reminder_icon.png', 'sad_cat.png',
        'send_icon.png', 'send_new_icon.png', 'surprise_cat.png'
    ];

    // ====== DOM 缓存 ======
    const $ = (sel) => document.querySelector(sel);
    const offsetXInput  = $('#offset-x');
    const offsetYInput  = $('#offset-y');
    const scaleInput    = $('#portrait-scale');
    const rotationInput = $('#portrait-rotation');
    const offsetXVal    = $('#offset-x-val');
    const offsetYVal    = $('#offset-y-val');
    const scaleVal      = $('#scale-val');
    const rotationVal   = $('#rotation-val');
    const cardName      = $('#card-preview-name');
    const placeholder   = $('#portrait-placeholder');
    const portraitCanvas = $('#card-portrait-canvas');
    const loadingOverlay = $('#model-loading-overlay');
    const backBtn       = $('#back-btn');
    const resetBtn      = $('#reset-composition-btn');
    const refreshBtn    = $('#refresh-preview-btn');
    const exportFullBtn = $('#export-full-btn');

    // ====== 初始化 ======
    document.addEventListener('DOMContentLoaded', async () => {
        // 禁用鼠标跟踪（导出页面不需要）
        window.mouseTrackingEnabled = false;

        bindEvents();

        // 从 URL 参数获取角色名并直接加载
        const params = new URLSearchParams(window.location.search);
        const name = params.get('name') || params.get('lanlan_name');
        if (name) {
            await onCharacterSelected(name);
        }
    });

    // ====== 事件绑定 ======
    function bindEvents() {
        // 构图滑块（实时预览由循环驱动，滑块仅更新参数）
        offsetXInput.addEventListener('input', () => {
            composition.offsetX = Number(offsetXInput.value);
            offsetXVal.textContent = composition.offsetX;
        });
        offsetYInput.addEventListener('input', () => {
            composition.offsetY = Number(offsetYInput.value);
            offsetYVal.textContent = composition.offsetY;
        });
        scaleInput.addEventListener('input', () => {
            composition.scale = Number(scaleInput.value);
            scaleVal.textContent = composition.scale + '%';
        });
        rotationInput.addEventListener('input', () => {
            composition.rotation = Number(rotationInput.value);
            rotationVal.textContent = composition.rotation + '°';
        });

        resetBtn.addEventListener('click', resetComposition);
        refreshBtn.addEventListener('click', () => refreshPreview());
        exportFullBtn.addEventListener('click', () => doExport('full'));
        backBtn.addEventListener('click', () => {
            if (window.opener) { window.close(); }
            else { window.history.back(); }
        });

        // 标签页切换
        document.querySelectorAll('.panel-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.panel-tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                const target = document.getElementById(tab.dataset.tab);
                if (target) target.classList.add('active');
            });
        });

        // 贴纸网格
        initStickerGrid();

        // 贴纸控件
        const stickerWRange = $('#sticker-w');
        const stickerWNum   = $('#sticker-w-num');
        const stickerHRange = $('#sticker-h');
        const stickerHNum   = $('#sticker-h-num');
        const lockRatioBox  = $('#sticker-lock-ratio');
        const stickerRotInput = $('#sticker-rotation');

        function applyStickerSize(axis, val) {
            const s = getSelectedSticker();
            if (!s) return;
            val = Math.max(1, val);
            if (lockRatioBox && lockRatioBox.checked && s.w > 0 && s.h > 0) {
                const ratio = s.w / s.h;
                if (axis === 'w') {
                    s.w = val;
                    s.h = Math.round(val / ratio);
                } else {
                    s.h = val;
                    s.w = Math.round(val * ratio);
                }
            } else {
                s[axis] = val;
            }
            syncStickerSizeUI(s);
            updateStickerElement(s);
        }

        function syncStickerSizeUI(s) {
            if (stickerWRange) stickerWRange.value = Math.min(s.w, 500);
            if (stickerWNum) stickerWNum.value = s.w;
            if (stickerHRange) stickerHRange.value = Math.min(s.h, 500);
            if (stickerHNum) stickerHNum.value = s.h;
        }

        if (stickerWRange) stickerWRange.addEventListener('input', () => applyStickerSize('w', Number(stickerWRange.value)));
        if (stickerWNum) stickerWNum.addEventListener('input', () => { let v = Number(stickerWNum.value); if (!isNaN(v)) applyStickerSize('w', v); });
        if (stickerHRange) stickerHRange.addEventListener('input', () => applyStickerSize('h', Number(stickerHRange.value)));
        if (stickerHNum) stickerHNum.addEventListener('input', () => { let v = Number(stickerHNum.value); if (!isNaN(v)) applyStickerSize('h', v); });
        if (stickerRotInput) {
            stickerRotInput.addEventListener('input', () => {
                const s = getSelectedSticker();
                if (!s) return;
                s.rotation = Number(stickerRotInput.value);
                $('#sticker-rotation-val').textContent = s.rotation + '°';
                updateStickerElement(s);
            });
        }
        const layerToggle = $('#sticker-layer-toggle');
        if (layerToggle) {
            layerToggle.addEventListener('click', () => {
                const s = getSelectedSticker();
                if (!s) return;
                s.layer = (s.layer === 'above') ? 'below' : 'above';
                updateLayerToggleUI(s);
                updateStickerOverlayOrder();
            });
        }
        const removeBtn = $('#remove-sticker-btn');
        const clearBtn = $('#clear-stickers-btn');
        if (removeBtn) removeBtn.addEventListener('click', removeSelectedSticker);
        if (clearBtn) clearBtn.addEventListener('click', clearAllStickers);

        // 支持在卡片预览区域拖拽偏移
        setupPreviewDrag();
    }

    // ====== 角色加载 ======
    async function onCharacterSelected(name) {
        if (!name) return;
        currentCharaName = name;
        cardName.textContent = name;

        showLoading(true);
        resetComposition();

        try {
            // 获取该角色的页面配置（包含模型类型和路径）
            const resp = await fetch(`/api/config/page_config?lanlan_name=${encodeURIComponent(name)}`);
            const cfg = await resp.json();
            if (!cfg || !cfg.success) {
                throw new Error(cfg?.error || '获取角色配置失败');
            }

            // 填充 lanlan_config（Live2D / VRM / MMD 初始化脚本依赖它）
            window.lanlan_config = window.lanlan_config || {};
            window.lanlan_config.lanlan_name = cfg.lanlan_name;
            window.lanlan_config.model_path = cfg.model_path;
            window.lanlan_config.model_type = cfg.model_type;
            window.lanlan_config.lighting = cfg.lighting;
            if (cfg.model_type === 'live3d') {
                window.lanlan_config.live3d_sub_type = cfg.live3d_sub_type;
            }

            // 确定实际模型类型
            let effectiveType = 'live2d';
            if (cfg.model_type === 'live3d') {
                effectiveType = (cfg.live3d_sub_type === 'mmd') ? 'mmd' : 'vrm';
            } else if (cfg.model_type === 'vrm') {
                effectiveType = 'vrm';
            }
            currentModelType = effectiveType;

            await loadCharacterModel(effectiveType, cfg);
        } catch (e) {
            console.error('[CardExport] 加载角色模型失败:', e);
            showLoading(false);
        }
    }

    // ====== 模型加载 ======
    async function loadCharacterModel(type, cfg) {
        isModelLoaded = false;
        stopPreviewLoop();

        // 先隐藏所有渲染容器
        const l2dContainer = $('#live2d-container');
        const vrmContainer = $('#vrm-container');
        const mmdContainer = $('#mmd-container');
        l2dContainer.style.display = 'none';
        vrmContainer.style.display = 'none';
        mmdContainer.style.display = 'none';

        try {
            if (type === 'live2d') {
                l2dContainer.style.display = '';
                await loadLive2DModel(cfg.model_path);
            } else if (type === 'vrm') {
                vrmContainer.style.display = '';
                await loadVRMModel(cfg.model_path, cfg.lighting);
            } else if (type === 'mmd') {
                mmdContainer.style.display = '';
                await loadMMDModel(cfg.model_path);
            }

            isModelLoaded = true;
            showLoading(false);

            // 确保模型加载后鼠标跟踪仍然禁用
            disableMouseTracking();

            // 启动持续预览循环
            startPreviewLoop();
        } catch (e) {
            console.error('[CardExport] 模型加载异常:', e);
            showLoading(false);
        }
    }

    async function loadLive2DModel(modelPath) {
        if (!window.live2dManager) {
            throw new Error('Live2D 管理器未就绪');
        }
        // 初始化 PIXI（如果尚未初始化），启用 preserveDrawingBuffer 以便截图
        if (!window.live2dManager.pixi_app) {
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container', {
                preserveDrawingBuffer: true
            });
        }
        await window.live2dManager.loadModel(modelPath);
    }

    async function loadVRMModel(modelPath, lighting) {
        // 等待 VRM 模块就绪
        await waitForCondition(() => window.vrmModuleLoaded, 10000, 'VRM 模块');

        if (!window.vrmManager) {
            const { VRMManager } = window;
            if (typeof VRMManager === 'function') {
                window.vrmManager = new VRMManager();
            } else {
                throw new Error('VRMManager 未定义');
            }
        }
        if (!window.vrmManager.renderer) {
            const canvas = document.getElementById('vrm-canvas');
            await window.vrmManager.initThreeJS(canvas);
        }
        if (lighting) {
            window.lanlan_config.lighting = lighting;
        }
        await window.vrmManager.loadModel(modelPath);
    }

    async function loadMMDModel(modelPath) {
        await waitForCondition(() => window.mmdModuleLoaded, 10000, 'MMD 模块');

        if (!window.mmdManager) {
            const { MMDManager } = window;
            if (typeof MMDManager === 'function') {
                window.mmdManager = new MMDManager();
            } else {
                throw new Error('MMDManager 未定义');
            }
        }
        if (!window.mmdManager.core?.renderer) {
            const canvas = document.getElementById('mmd-canvas');
            await window.mmdManager.initThreeJS(canvas);
        }
        await window.mmdManager.loadModel(modelPath);
    }

    /**
     * 禁用所有模型的鼠标跟踪效果
     */
    function disableMouseTracking() {
        window.mouseTrackingEnabled = false;
        if (window.live2dManager && typeof window.live2dManager.setMouseTrackingEnabled === 'function') {
            window.live2dManager.setMouseTrackingEnabled(false);
        }
        if (window.vrmManager && typeof window.vrmManager.setMouseTrackingEnabled === 'function') {
            window.vrmManager.setMouseTrackingEnabled(false);
        }
        if (window.mmdManager?.cursorFollow && typeof window.mmdManager.cursorFollow.setEnabled === 'function') {
            window.mmdManager.cursorFollow.setEnabled(false);
        }
    }

    // ====== 模型画布直接截图 ======

    /**
     * 获取当前活跃模型的渲染画布
     */
    function getModelCanvas() {
        if (currentModelType === 'live2d') {
            const mgr = window.live2dManager;
            if (mgr?.pixi_app?.renderer?.view) return mgr.pixi_app.renderer.view;
            return document.getElementById('live2d-canvas');
        }
        if (currentModelType === 'vrm') {
            const mgr = window.vrmManager;
            if (mgr?.renderer?.domElement) return mgr.renderer.domElement;
            return document.getElementById('vrm-canvas');
        }
        if (currentModelType === 'mmd') {
            const mgr = window.mmdManager;
            if (mgr?.core?.renderer?.domElement) return mgr.core.renderer.domElement;
            return document.getElementById('mmd-canvas');
        }
        return null;
    }

    /**
     * 在截图前确保渲染器输出最新帧
     */
    function ensureRender() {
        if (currentModelType === 'live2d') {
            const mgr = window.live2dManager;
            if (mgr?.pixi_app?.renderer && mgr?.pixi_app?.stage) {
                mgr.pixi_app.renderer.render(mgr.pixi_app.stage);
            }
        } else if (currentModelType === 'vrm') {
            const mgr = window.vrmManager;
            if (mgr?.renderer && mgr?.scene && mgr?.camera) {
                mgr.renderer.render(mgr.scene, mgr.camera);
            }
        } else if (currentModelType === 'mmd') {
            const core = window.mmdManager?.core;
            if (core?.renderer && core?.scene && core?.camera) {
                core.renderer.render(core.scene, core.camera);
            }
        }
    }

    /**
     * 将模型源画布直接绘制到目标 context 上，应用构图参数
     * 预览和导出共用此函数，确保所见即所得
     *
     * @param {CanvasRenderingContext2D} ctx  目标 context
     * @param {HTMLCanvasElement} srcCanvas   模型渲染画布（全分辨率）
     * @param {number} outW  目标绘制区域宽度（CSS 像素）
     * @param {number} outH  目标绘制区域高度（CSS 像素）
     */
    function drawModelWithComposition(ctx, srcCanvas, outW, outH) {
        // 从源画布中裁剪出 3:4 比例的区域（cover 语义）
        const srcAspect = srcCanvas.width / srcCanvas.height;
        const dstAspect = outW / outH;           // ≈ 0.75 (3:4)
        let sx = 0, sy = 0, sw = srcCanvas.width, sh = srcCanvas.height;

        if (srcAspect > dstAspect) {
            // 源更宽 → 裁两侧
            sw = srcCanvas.height * dstAspect;
            sx = (srcCanvas.width - sw) / 2;
        } else {
            // 源更高 → 裁上下
            sh = srcCanvas.width / dstAspect;
            sy = (srcCanvas.height - sh) / 2;
        }

        const scale = composition.scale / 100;
        const drawW = outW * scale;
        const drawH = outH * scale;

        // 偏移量在 450×600 坐标系下定义，按实际尺寸等比缩放
        const ratio = outW / 450;
        const dx = (outW - drawW) / 2 + composition.offsetX * ratio;
        const dy = (outH - drawH) / 2 + composition.offsetY * ratio;

        // 应用旋转
        const angle = composition.rotation * Math.PI / 180;
        if (angle !== 0) {
            ctx.save();
            ctx.translate(outW / 2, outH / 2);
            ctx.rotate(angle);
            ctx.translate(-outW / 2, -outH / 2);
        }

        ctx.drawImage(srcCanvas, sx, sy, sw, sh, dx, dy, drawW, drawH);

        if (angle !== 0) {
            ctx.restore();
        }
    }

    // ====== 预览循环 ======

    /**
     * 启动持续预览刷新（~15fps，用 requestAnimationFrame 节流）
     */
    function startPreviewLoop() {
        stopPreviewLoop();
        lastPreviewTime = 0;

        function loop(timestamp) {
            previewLoopId = requestAnimationFrame(loop);
            if (timestamp - lastPreviewTime < 66) return;
            lastPreviewTime = timestamp;
            refreshPreview();
        }
        previewLoopId = requestAnimationFrame(loop);
    }

    function stopPreviewLoop() {
        if (previewLoopId != null) {
            cancelAnimationFrame(previewLoopId);
            previewLoopId = null;
        }
    }

    function refreshPreview() {
        if (!isModelLoaded) return;

        const srcCanvas = getModelCanvas();
        if (!srcCanvas || srcCanvas.width <= 0 || srcCanvas.height <= 0) return;

        ensureRender();

        const ctx = portraitCanvas.getContext('2d');
        const areaEl = $('#card-portrait-area');
        const w = areaEl.clientWidth;
        const h = areaEl.clientHeight;
        if (w <= 0 || h <= 0) return;

        const dpr = window.devicePixelRatio || 1;
        const needW = Math.round(w * dpr);
        const needH = Math.round(h * dpr);
        if (portraitCanvas.width !== needW || portraitCanvas.height !== needH) {
            portraitCanvas.width = needW;
            portraitCanvas.height = needH;
            portraitCanvas.style.width = w + 'px';
            portraitCanvas.style.height = h + 'px';
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);

        drawModelWithComposition(ctx, srcCanvas, w, h);
        // 注意：贴纸通过 DOM 覆盖层显示在预览中，无需绘制到 canvas
        placeholder.classList.add('hidden');
    }

    // ====== 预览区域拖拽 ======
    function setupPreviewDrag() {
        const previewEl = $('#card-preview');
        let dragging = false;
        let startX = 0, startY = 0;
        let startOX = 0, startOY = 0;

        previewEl.addEventListener('pointerdown', (e) => {
            if (!isModelLoaded) return;
            dragging = true;
            startX = e.clientX;
            startY = e.clientY;
            startOX = composition.offsetX;
            startOY = composition.offsetY;
            previewEl.setPointerCapture(e.pointerId);
        });

        previewEl.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            const previewScale = $('#card-portrait-area').clientWidth / 450;
            composition.offsetX = Math.round(startOX + (e.clientX - startX) / previewScale);
            composition.offsetY = Math.round(startOY + (e.clientY - startY) / previewScale);

            // 同步滑块
            offsetXInput.value = clamp(composition.offsetX, -500, 500);
            offsetYInput.value = clamp(composition.offsetY, -500, 500);
            offsetXVal.textContent = composition.offsetX;
            offsetYVal.textContent = composition.offsetY;
        });

        const stopDrag = () => { dragging = false; };
        previewEl.addEventListener('pointerup', stopDrag);
        previewEl.addEventListener('pointercancel', stopDrag);

        // 滚轮缩放
        previewEl.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -5 : 5;
            composition.scale = clamp(composition.scale + delta, 50, 300);
            scaleInput.value = composition.scale;
            scaleVal.textContent = composition.scale + '%';
        }, { passive: false });
    }

    // ====== 导出 ======
    async function doExport(type) {
        if (!currentCharaName) return;

        try {
            let response;

            exportFullBtn.disabled = true;
            exportFullBtn.textContent = t('cardExport.exporting', '导出中...');

            // 用调整后的构图参数渲染最终立绘
            const portraitBlob = await renderFinalPortrait();

            if (portraitBlob) {
                const formData = new FormData();
                formData.append('portrait', portraitBlob, 'portrait.png');
                formData.append('include_model', 'true');

                response = await fetch(
                    `/api/characters/catgirl/${encodeURIComponent(currentCharaName)}/export-with-portrait`,
                    { method: 'POST', body: formData }
                );
            } else {
                response = await fetch(
                    `/api/characters/catgirl/${encodeURIComponent(currentCharaName)}/export`,
                    { method: 'GET' }
                );
            }

            exportFullBtn.disabled = false;
            exportFullBtn.textContent = t('cardExport.exportFull', '导出角色卡');

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.error || `HTTP ${response.status}`);
            }

            const blob = await response.blob();
            const filename = parseFilename(response);
            await saveFile(blob, filename);
        } catch (e) {
            console.error('[CardExport] 导出失败:', e);
            alert(t('cardExport.exportError', '导出失败: ') + e.message);
            exportFullBtn.disabled = false;
            exportFullBtn.textContent = t('cardExport.exportFull', '导出角色卡');
        }
    }

    /**
     * 根据构图参数渲染最终立绘 Blob
     * 输出尺寸与后端卡片立绘区域完全一致（600 × (800 - 800//6)），确保所见即所得
     */
    async function renderFinalPortrait() {
        const srcCanvas = getModelCanvas();
        if (!srcCanvas || srcCanvas.width <= 0 || srcCanvas.height <= 0) return null;

        ensureRender();

        // 与后端卡片尺寸保持一致：600×800，header = Math.floor(800/6) = 133
        const cardW = 600, cardH = 800;
        const headerH = Math.floor(cardH / 6);
        const outW = cardW;
        const outH = cardH - headerH;

        const outCanvas = document.createElement('canvas');
        outCanvas.width = outW;
        outCanvas.height = outH;
        const ctx = outCanvas.getContext('2d');

        // 绘制顺序：模型下方贴纸 → 模型 → 模型上方贴纸
        const belowStickers = stickers.filter(s => s.layer === 'below');
        const aboveStickers = stickers.filter(s => s.layer === 'above');

        if (belowStickers.length > 0) {
            await drawStickerList(ctx, belowStickers, outW, outH);
        }

        drawModelWithComposition(ctx, srcCanvas, outW, outH);

        if (aboveStickers.length > 0) {
            await drawStickerList(ctx, aboveStickers, outW, outH);
        }

        return new Promise((resolve) => {
            outCanvas.toBlob((blob) => resolve(blob), 'image/png');
        });
    }

    // ====== 贴纸系统 ======

    function initStickerGrid() {
        const grid = $('#sticker-grid');
        if (!grid) return;
        STICKER_FILES.forEach(file => {
            const item = document.createElement('div');
            item.className = 'sticker-item';
            const img = document.createElement('img');
            img.src = `/static/icons/${file}`;
            img.alt = file.replace(/\.\w+$/, '');
            img.draggable = false;
            item.appendChild(img);
            item.addEventListener('click', () => addSticker(`/static/icons/${file}`));
            grid.appendChild(item);
        });
    }

    function addSticker(src) {
        const overlay = $('#sticker-overlay');
        if (!overlay) return;

        const id = ++stickerIdCounter;
        const sticker = { id, src, x: 50, y: 50, w: 60, h: 60, rotation: 0, layer: 'above', imgEl: null };

        const el = document.createElement('img');
        el.src = src;
        el.className = 'sticker-placed';
        el.draggable = false;
        el.dataset.stickerId = id;
        sticker.imgEl = el;

        updateStickerElement(sticker);
        overlay.appendChild(el);
        stickers.push(sticker);

        // 选中新贴纸
        selectSticker(id);
        updateStickerOverlayOrder();

        // 贴纸拖拽
        setupStickerDrag(sticker, el);
    }

    function updateStickerElement(s) {
        const el = s.imgEl;
        if (!el) return;
        el.style.width = s.w + 'px';
        el.style.height = s.h + 'px';
        el.style.left = `calc(${s.x}% - ${s.w / 2}px)`;
        el.style.top = `calc(${s.y}% - ${s.h / 2}px)`;
        el.style.transform = `rotate(${s.rotation}deg)`;
    }

    function setupStickerDrag(sticker, el) {
        let dragging = false;
        let startX, startY, startPctX, startPctY;

        el.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
            dragging = true;
            startX = e.clientX;
            startY = e.clientY;
            startPctX = sticker.x;
            startPctY = sticker.y;
            el.setPointerCapture(e.pointerId);
            selectSticker(sticker.id);
        });

        el.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            e.stopPropagation();
            const area = $('#card-portrait-area');
            const rect = area.getBoundingClientRect();
            const dx = (e.clientX - startX) / rect.width * 100;
            const dy = (e.clientY - startY) / rect.height * 100;
            sticker.x = clamp(startPctX + dx, 0, 100);
            sticker.y = clamp(startPctY + dy, 0, 100);
            updateStickerElement(sticker);
        });

        const stop = () => { dragging = false; };
        el.addEventListener('pointerup', stop);
        el.addEventListener('pointercancel', stop);
    }

    function selectSticker(id) {
        selectedStickerId = id;
        // 更新视觉选中状态
        document.querySelectorAll('.sticker-placed').forEach(el => {
            el.classList.toggle('selected', Number(el.dataset.stickerId) === id);
        });

        const s = getSelectedSticker();
        const controls = $('#sticker-controls');
        if (s && controls) {
            controls.style.display = '';
            // 同步宽高 UI
            const wr = $('#sticker-w'), wn = $('#sticker-w-num');
            const hr = $('#sticker-h'), hn = $('#sticker-h-num');
            if (wr) wr.value = Math.min(s.w, 500);
            if (wn) wn.value = s.w;
            if (hr) hr.value = Math.min(s.h, 500);
            if (hn) hn.value = s.h;
            $('#sticker-rotation').value = s.rotation;
            $('#sticker-rotation-val').textContent = s.rotation + '°';
            updateLayerToggleUI(s);
        } else if (controls) {
            controls.style.display = 'none';
        }
    }

    function updateLayerToggleUI(s) {
        const btn = $('#sticker-layer-toggle');
        if (!btn) return;
        if (s.layer === 'above') {
            btn.textContent = t('cardExport.layerAbove', '模型上方');
            btn.title = t('cardExport.layerToggleHint', '点击切换到模型下方');
        } else {
            btn.textContent = t('cardExport.layerBelow', '模型下方');
            btn.title = t('cardExport.layerToggleHint', '点击切换到模型上方');
        }
    }

    /**
     * 根据贴纸图层设置更新DOM覆盖层顺序
     * below的贴纸放入 sticker-overlay-below（canvas 下方）
     * above的贴纸放入 sticker-overlay（canvas 上方）
     */
    function updateStickerOverlayOrder() {
        const above = $('#sticker-overlay');
        const below = $('#sticker-overlay-below');
        if (!above || !below) return;
        stickers.forEach(s => {
            const target = (s.layer === 'below') ? below : above;
            if (s.imgEl.parentElement !== target) {
                target.appendChild(s.imgEl);
            }
        });
    }

    function getSelectedSticker() {
        return stickers.find(s => s.id === selectedStickerId) || null;
    }

    function removeSelectedSticker() {
        const idx = stickers.findIndex(s => s.id === selectedStickerId);
        if (idx === -1) return;
        stickers[idx].imgEl.remove();
        stickers.splice(idx, 1);
        selectedStickerId = null;
        selectSticker(null);
        updateStickerOverlayOrder();
    }

    function clearAllStickers() {
        stickers.forEach(s => s.imgEl.remove());
        stickers.length = 0;
        selectedStickerId = null;
        selectSticker(null);
    }

    /**
     * 将指定贴纸列表绘制到 canvas context 上
     * @param {CanvasRenderingContext2D} ctx
     * @param {Array} stickerList  要绘制的贴纸数组
     * @param {number} outW  目标宽度
     * @param {number} outH  目标高度
     */
    async function drawStickerList(ctx, stickerList, outW, outH) {
        for (const s of stickerList) {
            const img = await loadImage(s.src);
            const scale = outW / ($('#card-portrait-area')?.clientWidth || 450);
            const drawW = s.w * scale;
            const drawH = s.h * scale;
            const cx = s.x / 100 * outW;
            const cy = s.y / 100 * outH;
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate(s.rotation * Math.PI / 180);
            ctx.drawImage(img, -drawW / 2, -drawH / 2, drawW, drawH);
            ctx.restore();
        }
    }

    function loadImage(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
        });
    }

    // ====== 工具函数 ======
    function t(key, fallback) {
        if (window.i18next && typeof window.i18next.t === 'function') {
            const val = window.i18next.t(key);
            if (val && val !== key) return val;
        }
        if (window.t && typeof window.t === 'function') {
            const val = window.t(key);
            if (val && val !== key) return val;
        }
        return fallback;
    }

    function clamp(v, min, max) {
        return Math.min(max, Math.max(min, v));
    }

    function showLoading(show) {
        if (show) {
            loadingOverlay.classList.remove('hidden');
        } else {
            loadingOverlay.classList.add('hidden');
        }
    }

    function resetComposition() {
        composition.offsetX = 0;
        composition.offsetY = 0;
        composition.scale = 100;
        composition.rotation = 0;
        offsetXInput.value = 0;
        offsetYInput.value = 0;
        scaleInput.value = 100;
        rotationInput.value = 0;
        offsetXVal.textContent = '0';
        offsetYVal.textContent = '0';
        scaleVal.textContent = '100%';
        rotationVal.textContent = '0°';
    }

    function waitForCondition(condFn, timeoutMs, label) {
        return new Promise((resolve, reject) => {
            if (condFn()) { resolve(); return; }
            const start = Date.now();
            const check = setInterval(() => {
                if (condFn()) { clearInterval(check); resolve(); }
                else if (Date.now() - start > timeoutMs) {
                    clearInterval(check);
                    reject(new Error(`等待 ${label} 超时`));
                }
            }, 100);
        });
    }

    function parseFilename(response) {
        const cd = response.headers.get('Content-Disposition');
        let filename = `${currentCharaName}_角色卡.png`;

        if (cd) {
            const starMatch = cd.match(/filename\*=UTF-8''([^;]+)/i);
            if (starMatch) {
                try { filename = decodeURIComponent(starMatch[1]); } catch (_) { /* ignore */ }
            } else {
                const match = cd.match(/filename="([^"]+)"/i);
                if (match) filename = match[1];
            }
        }
        return filename;
    }

    async function saveFile(blob, filename) {
        try {
            if ('showSaveFilePicker' in window) {
                const handle = await window.showSaveFilePicker({
                    suggestedName: filename,
                    types: [{ description: 'PNG 图片', accept: { 'image/png': ['.png'] } }]
                });
                const writable = await handle.createWritable();
                await writable.write(blob);
                await writable.close();
                return;
            }
        } catch (e) {
            if (e.name === 'AbortError') return; // 用户取消
        }
        // fallback
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
})();
