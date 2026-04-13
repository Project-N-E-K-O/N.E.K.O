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
    const composition = { offsetX: 0, offsetY: 0, scale: 100 };

    // ====== DOM 缓存 ======
    const $ = (sel) => document.querySelector(sel);
    const charSelect   = $('#character-select');
    const offsetXInput  = $('#offset-x');
    const offsetYInput  = $('#offset-y');
    const scaleInput    = $('#portrait-scale');
    const offsetXVal    = $('#offset-x-val');
    const offsetYVal    = $('#offset-y-val');
    const scaleVal      = $('#scale-val');
    const cardName      = $('#card-preview-name');
    const placeholder   = $('#portrait-placeholder');
    const portraitCanvas = $('#card-portrait-canvas');
    const loadingOverlay = $('#model-loading-overlay');
    const backBtn       = $('#back-btn');
    const resetBtn      = $('#reset-composition-btn');
    const refreshBtn    = $('#refresh-preview-btn');
    const exportFullBtn = $('#export-full-btn');
    const exportSetBtn  = $('#export-settings-btn');

    // ====== 初始化 ======
    document.addEventListener('DOMContentLoaded', async () => {
        bindEvents();
        await loadCharacterList();

        // 如果 URL 带有角色名参数，自动选中
        const params = new URLSearchParams(window.location.search);
        const name = params.get('name') || params.get('lanlan_name');
        if (name && charSelect.querySelector(`option[value="${CSS.escape(name)}"]`)) {
            charSelect.value = name;
        }
        if (charSelect.value) {
            await onCharacterSelected(charSelect.value);
        }
    });

    // ====== 事件绑定 ======
    function bindEvents() {
        charSelect.addEventListener('change', () => onCharacterSelected(charSelect.value));

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

        resetBtn.addEventListener('click', resetComposition);
        refreshBtn.addEventListener('click', () => refreshPreview());
        exportFullBtn.addEventListener('click', () => doExport('full'));
        exportSetBtn.addEventListener('click', () => doExport('settings-only'));
        backBtn.addEventListener('click', () => {
            if (window.opener) { window.close(); }
            else { window.history.back(); }
        });

        // 支持在卡片预览区域拖拽偏移
        setupPreviewDrag();
    }

    // ====== 角色列表 ======
    async function loadCharacterList() {
        try {
            const resp = await fetch('/api/characters');
            const data = await resp.json();
            const catgirls = data['猫娘'] || data.catgirls || {};
            charSelect.innerHTML = '';

            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = t('cardExport.selectCharacterHint', '-- 请选择角色 --');
            charSelect.appendChild(defaultOpt);

            for (const name of Object.keys(catgirls)) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                charSelect.appendChild(opt);
            }
        } catch (e) {
            console.error('[CardExport] 获取角色列表失败:', e);
        }
    }

    // ====== 角色切换 ======
    async function onCharacterSelected(name) {
        if (!name) return;
        currentCharaName = name;
        cardName.textContent = name;
        exportFullBtn.disabled = true;

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
            exportFullBtn.disabled = false;
            showLoading(false);

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

        ctx.drawImage(srcCanvas, sx, sy, sw, sh, dx, dy, drawW, drawH);
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

            if (type === 'full') {
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
            } else {
                response = await fetch(
                    `/api/characters/catgirl/${encodeURIComponent(currentCharaName)}/export-settings`,
                    { method: 'GET' }
                );
            }

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.error || `HTTP ${response.status}`);
            }

            const blob = await response.blob();
            const filename = parseFilename(response, type);
            await saveFile(blob, filename, type);
        } catch (e) {
            console.error('[CardExport] 导出失败:', e);
            alert(t('cardExport.exportError', '导出失败: ') + e.message);
            exportFullBtn.disabled = false;
            exportFullBtn.textContent = t('cardExport.exportFull', '导出角色卡');
        }
    }

    /**
     * 根据构图参数渲染最终 450×600 立绘 Blob
     * 使用与预览完全相同的 drawModelWithComposition，确保所见即所得
     */
    async function renderFinalPortrait() {
        const srcCanvas = getModelCanvas();
        if (!srcCanvas || srcCanvas.width <= 0 || srcCanvas.height <= 0) return null;

        ensureRender();

        const outCanvas = document.createElement('canvas');
        outCanvas.width = 450;
        outCanvas.height = 600;
        const ctx = outCanvas.getContext('2d');

        drawModelWithComposition(ctx, srcCanvas, 450, 600);

        return new Promise((resolve) => {
            outCanvas.toBlob((blob) => resolve(blob), 'image/png');
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
        offsetXInput.value = 0;
        offsetYInput.value = 0;
        scaleInput.value = 100;
        offsetXVal.textContent = '0';
        offsetYVal.textContent = '0';
        scaleVal.textContent = '100%';
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

    function parseFilename(response, type) {
        const cd = response.headers.get('Content-Disposition');
        let filename = type === 'settings-only'
            ? `${currentCharaName}_设定.nekocfg`
            : `${currentCharaName}_角色卡.png`;

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

    async function saveFile(blob, filename, type) {
        try {
            if ('showSaveFilePicker' in window) {
                const handle = await window.showSaveFilePicker({
                    suggestedName: filename,
                    types: type === 'settings-only'
                        ? [{ description: 'NEKO 设定文件', accept: { 'application/octet-stream': ['.nekocfg'] } }]
                        : [{ description: 'PNG 图片', accept: { 'image/png': ['.png'] } }]
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
