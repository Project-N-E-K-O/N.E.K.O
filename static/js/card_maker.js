/**
 * card_maker.js – 卡面制作页面交互逻辑
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
    let modelLayerSelected = false;  // 图层面板中模型是否被选中

    // 当前激活的标签页: 'model-tab' | 'decor-tab'
    let activeTab = 'model-tab';

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

    // ====== maker 模式检测 ======
    const _urlParams = new URLSearchParams(window.location.search);
    const isMakerMode = _urlParams.get('mode') === 'maker';

    // ====== 初始化 ======
    document.addEventListener('DOMContentLoaded', async () => {
        // 禁用鼠标跟踪（导出页面不需要）
        window.mouseTrackingEnabled = false;

        // 设置标题和按钮（maker 模式与导出模式使用不同文案）
        const titleEl = document.querySelector('.page-title-bar h2');
        if (isMakerMode) {
            document.title = (window.t ? window.t('cardExport.title') : '卡面制作') + ' - Project N.E.K.O.';
            if (titleEl) {
                titleEl.textContent = window.t ? window.t('cardExport.title') : '卡面制作';
            }
            if (exportFullBtn) {
                exportFullBtn.textContent = window.t ? window.t('cardExport.saveCardFace') : '保存卡面';
                exportFullBtn.setAttribute('data-i18n', 'cardExport.saveCardFace');
            }
        } else {
            document.title = (window.t ? window.t('cardExport.exportTitle') : '导出角色卡') + ' - Project N.E.K.O.';
            if (titleEl) {
                titleEl.textContent = window.t ? window.t('cardExport.exportTitle') : '导出角色卡';
            }
            if (exportFullBtn) {
                exportFullBtn.textContent = window.t ? window.t('cardExport.exportFull', '导出角色卡') : '导出角色卡';
                exportFullBtn.setAttribute('data-i18n', 'cardExport.exportFull');
            }
        }

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
        exportFullBtn.addEventListener('click', () => {
            if (isMakerMode) { doSaveCardFace(); }
            else { doExport('full'); }
        });
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
                activeTab = tab.dataset.tab;
                updateStickerInteractivity();
                if (activeTab === 'model-tab') {
                    selectSticker(null);
                }
                refreshLayerPanel();
            });
        });

        // 贴纸网格
        initStickerGrid();

        // 贴纸控件
        const stickerWRange = $('#sticker-w');
        const stickerWVal   = $('#sticker-w-val');
        const stickerHRange = $('#sticker-h');
        const stickerHVal   = $('#sticker-h-val');
        const lockRatioBox  = $('#sticker-lock-ratio');
        const stickerRotInput = $('#sticker-rotation');

        // 锁定比例按钮切换
        if (lockRatioBox) {
            lockRatioBox.addEventListener('click', () => {
                lockRatioBox.classList.toggle('active');
            });
        }

        function applyStickerSize(axis, val) {
            const s = getSelectedSticker();
            if (!s) return;
            val = Math.max(1, val);
            if (lockRatioBox && lockRatioBox.classList.contains('active') && s.w > 0 && s.h > 0) {
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
            if (stickerWRange) stickerWRange.value = Math.min(s.w, 2000);
            if (stickerWVal) stickerWVal.textContent = s.w + 'px';
            if (stickerHRange) stickerHRange.value = Math.min(s.h, 2000);
            if (stickerHVal) stickerHVal.textContent = s.h + 'px';
        }

        if (stickerWRange) stickerWRange.addEventListener('input', () => applyStickerSize('w', Number(stickerWRange.value)));
        if (stickerHRange) stickerHRange.addEventListener('input', () => applyStickerSize('h', Number(stickerHRange.value)));
        if (stickerRotInput) {
            stickerRotInput.addEventListener('input', () => {
                const s = getSelectedSticker();
                if (!s) return;
                s.rotation = Number(stickerRotInput.value);
                $('#sticker-rotation-val').textContent = s.rotation + '°';
                updateStickerElement(s);
            });
        }
        const clearBtn = $('#clear-stickers-btn');
        if (clearBtn) clearBtn.addEventListener('click', clearAllStickers);

        // 支持在卡片预览区域拖拽偏移
        setupPreviewDrag();
        setupRotateHandle();
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

        // 将模型居中（默认布局放在右下角，导出页面需要居中）
        const model = window.live2dManager.currentModel;
        if (model) {
            const screen = window.live2dManager.pixi_app.renderer.screen;
            model.anchor.set(0.5, 0.5);
            model.x = screen.width / 2;
            model.y = screen.height / 2;
        }
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
            await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container');
        }
        if (lighting) {
            window.lanlan_config.lighting = lighting;
        }
        await window.vrmManager.loadModel(modelPath);
        // 重置相机：让模型居中填满画布（忽略主页面保存的相机位置）
        centerThreeCamera(window.vrmManager);
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
            await window.mmdManager.init('mmd-canvas', 'mmd-container');
        }
        await window.mmdManager.loadModel(modelPath);
        // 重置相机：让模型居中填满画布
        const mmdProxy = {
            scene: window.mmdManager.core?.scene,
            camera: window.mmdManager.core?.camera,
            renderer: window.mmdManager.core?.renderer
        };
        centerThreeCamera(mmdProxy);
    }

    /**
     * 将 Three.js 相机重置为正对模型中心，模型高度填满画布约 85%
     * 适用于 VRM / MMD 的 manager 对象（需具有 scene, camera, renderer）
     */
    function centerThreeCamera(mgr) {
        const THREE = window.THREE;
        if (!THREE || !mgr?.scene || !mgr?.camera || !mgr?.renderer) return;
        try {
            const box = new THREE.Box3().setFromObject(mgr.scene);
            if (box.isEmpty()) return;
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            const modelHeight = size.y > 0 ? size.y : 1.5;

            // 用画布实际高度计算，让模型占约 85% 高度
            const canvasH = mgr.renderer.domElement.height || window.innerHeight;
            const fillRatio = 0.85;
            const fov = mgr.camera.fov * (Math.PI / 180);
            const distance = (modelHeight / 2) / Math.tan(fov / 2) / fillRatio;

            mgr.camera.position.set(center.x, center.y, center.z + Math.abs(distance));
            mgr.camera.lookAt(center.x, center.y, center.z);
            mgr.camera.updateProjectionMatrix();

            // 同步 _cameraTarget（VRM 用）
            if (mgr._cameraTarget) {
                mgr._cameraTarget.set(center.x, center.y, center.z);
            }
            // 同步 OrbitControls（如果存在）
            if (mgr.controls) {
                mgr.controls.target.set(center.x, center.y, center.z);
                mgr.controls.update();
            }
        } catch (e) {
            console.warn('[CardExport] centerThreeCamera 失败:', e);
        }
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

        // 应用旋转（围绕模型中心）
        const angle = composition.rotation * Math.PI / 180;
        if (angle !== 0) {
            const cx = dx + drawW / 2;
            const cy = dy + drawH / 2;
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate(angle);
            ctx.translate(-cx, -cy);
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
            if (activeTab !== 'model-tab' && !modelLayerSelected) return;
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
            composition.offsetX = clamp(Math.round(startOX + (e.clientX - startX) / previewScale), -500, 500);
            composition.offsetY = clamp(Math.round(startOY + (e.clientY - startY) / previewScale), -500, 500);

            // 同步滑块
            offsetXInput.value = composition.offsetX;
            offsetYInput.value = composition.offsetY;
            offsetXVal.textContent = composition.offsetX;
            offsetYVal.textContent = composition.offsetY;
        });

        const stopDrag = () => { dragging = false; };
        previewEl.addEventListener('pointerup', stopDrag);
        previewEl.addEventListener('pointercancel', stopDrag);

        // 滚轮：模型 tab 缩放模型，装饰 tab 缩放选中贴纸或模型
        previewEl.addEventListener('wheel', (e) => {
            e.preventDefault();
            if (activeTab === 'model-tab' || modelLayerSelected) {
                const delta = e.deltaY > 0 ? -5 : 5;
                composition.scale = clamp(composition.scale + delta, 50, 300);
                scaleInput.value = composition.scale;
                scaleVal.textContent = composition.scale + '%';
            } else if (activeTab === 'decor-tab') {
                const s = getSelectedSticker();
                if (!s) return;
                const factor = e.deltaY > 0 ? 0.95 : 1.05;
                s.w = clamp(Math.round(s.w * factor), 1, 2000);
                s.h = clamp(Math.round(s.h * factor), 1, 2000);
                _syncStickerSizeUI(s);
                updateStickerElement(s);
            }
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

    // ====== 保存卡面（maker 模式专用） ======
    async function doSaveCardFace() {
        if (!currentCharaName) return;

        try {
            exportFullBtn.disabled = true;
            exportFullBtn.textContent = t('cardExport.savingCardFace', '保存中...');

            const cardBlob = await renderFullCard();
            if (!cardBlob) {
                throw new Error(t('cardExport.renderFailed', '无法渲染卡面图片'));
            }

            const formData = new FormData();
            formData.append('image', cardBlob, 'card_face.png');

            const response = await fetch(
                `/api/characters/catgirl/${encodeURIComponent(currentCharaName)}/card-face`,
                { method: 'PUT', body: formData }
            );

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.error || `HTTP ${response.status}`);
            }

            const respJson = await response.json().catch(() => ({}));
            if (respJson.partial_success) {
                exportFullBtn.textContent = t('cardExport.saveCardFacePartialSuccess', 'PNG 已保存，但元数据写入失败: {{error}}', { error: respJson.error || '' });
                exportFullBtn.disabled = false;
                return;
            }

            // 通知父窗口更新卡面
            if (window.opener) {
                try {
                    window.opener.postMessage({
                        type: 'card-face-updated',
                        name: currentCharaName,
                        timestamp: Date.now()
                    }, window.location.origin);
                } catch (_) {}

                // 额外触发父窗口刷新列表，确保跨窗口场景下也能立刻看到新卡面
                try {
                    const loadCharacterCards = window.opener.loadCharacterCards;
                    if (typeof loadCharacterCards === 'function') {
                        const refreshResult = loadCharacterCards.call(window.opener);
                        if (refreshResult && typeof refreshResult.catch === 'function') {
                            refreshResult.catch(() => {});
                        }
                    }
                } catch (_) {}
            }

            exportFullBtn.textContent = t('cardExport.saveCardFaceSuccess', '保存成功！');
            setTimeout(() => {
                exportFullBtn.disabled = false;
                exportFullBtn.textContent = t('cardExport.saveCardFace', '保存卡面');
            }, 1500);
        } catch (e) {
            console.error('[CardMaker] 保存卡面失败:', e);
            alert(t('cardExport.saveCardFaceFailed', '保存失败: ' + e.message, { error: e.message }));
            exportFullBtn.disabled = false;
            exportFullBtn.textContent = t('cardExport.saveCardFace', '保存卡面');
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
        // 按 layerOrder 排序确保与预览一致
        const stickerOrder = layerOrder
            .filter(e => e.type === 'sticker')
            .map(e => stickers.find(s => s.id === e.id))
            .filter(Boolean);
        const belowStickers = stickerOrder.filter(s => s.layer === 'below');
        const aboveStickers = stickerOrder.filter(s => s.layer === 'above');

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

    /**
     * 渲染完整角色卡（蓝色头部 + 角色名 + 立绘）用于卡面保存
     * 输出尺寸 600×800，与后端角色卡完全一致
     */
    async function renderFullCard() {
        const portraitBlob = await renderFinalPortrait();
        if (!portraitBlob) {
            console.warn('[card_maker] renderFinalPortrait returned null, aborting card render');
            return null;
        }

        const cardW = 600, cardH = 800;
        const headerH = Math.floor(cardH / 6); // 133px

        const outCanvas = document.createElement('canvas');
        outCanvas.width = cardW;
        outCanvas.height = cardH;
        const ctx = outCanvas.getContext('2d');

        // 底色
        ctx.fillStyle = '#E8F4F8';
        ctx.fillRect(0, 0, cardW, cardH);

        // 蓝色头部
        ctx.fillStyle = '#40C5F1';
        ctx.fillRect(0, 0, cardW, headerH);

        // 角色名称（白色，带阴影）
        const displayName = currentCharaName || '';
        ctx.font = 'bold 42px "Microsoft YaHei", "SimHei", "PingFang SC", sans-serif';
        ctx.textBaseline = 'middle';

        // 阴影
        ctx.fillStyle = 'rgba(0, 0, 0, 0.25)';
        ctx.fillText(displayName, 42, headerH / 2 + 2);

        // 白色文字
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText(displayName, 40, headerH / 2);

        // 绘制立绘到头部下方
        if (portraitBlob) {
            const portraitImg = await createImageBitmap(portraitBlob);
            ctx.drawImage(portraitImg, 0, headerH, cardW, cardH - headerH);
            portraitImg.close();
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
        });        // "导入自定义贴纸"按钮
        const importItem = document.createElement('div');
        importItem.className = 'sticker-item sticker-import-btn';
        importItem.innerHTML = '<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
        importItem.title = t('cardExport.importSticker', '导入自定义贴纸');
        importItem.addEventListener('click', () => importCustomSticker());
        grid.appendChild(importItem);

        // 从 localStorage 恢复已保存的自定义贴纸
        loadCustomStickers();
    }

    const STICKER_SIZE_LIMIT = 5 * 1024 * 1024; // 5MB

    function compressStickerImage(dataUrl, maxSize = 1024) {
        return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
                let { width, height } = img;
                if (width > maxSize || height > maxSize) {
                    const ratio = Math.min(maxSize / width, maxSize / height);
                    width = Math.round(width * ratio);
                    height = Math.round(height * ratio);
                }
                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, width, height);
                resolve(canvas.toDataURL('image/png'));
            };
            img.onerror = () => resolve(dataUrl);
            img.src = dataUrl;
        });
    }

    function importCustomSticker() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.style.display = 'none';
        input.addEventListener('change', () => {
            const file = input.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = async (e) => {
                const dataUrl = e.target.result;
                const byteSize = dataUrl.length * 3 / 4; // base64 → 实际字节估算
                if (byteSize > STICKER_SIZE_LIMIT) {
                    const choice = await showConfirm(
                        t('cardExport.stickerSizeWarning',
                          '该图片较大（超过 5MB），压缩后可永久保存。\n选择「取消」将作为临时贴纸使用（关闭页面后消失）。'),
                        t('cardExport.stickerSizeTitle', '贴纸图片过大'),
                        {
                            okText: t('cardExport.compressAndSave', '压缩并保存'),
                            cancelText: t('cardExport.useTemporary', '临时使用')
                        }
                    );
                    if (choice) {
                        const compressed = await compressStickerImage(dataUrl);
                        addCustomStickerToGrid(compressed, true);
                    } else {
                        addCustomStickerToGrid(dataUrl, false, true);
                    }
                } else {
                    addCustomStickerToGrid(dataUrl, true);
                }
            };
            reader.readAsDataURL(file);
            input.remove();
        });
        document.body.appendChild(input);
        input.click();
    }

    function addCustomStickerToGrid(dataUrl, save = true, temporary = false) {
        const grid = $('#sticker-grid');
        const importBtn = grid.querySelector('.sticker-import-btn');
        if (!grid) return;

        const item = document.createElement('div');
        item.className = 'sticker-item sticker-custom' + (temporary ? ' sticker-temporary' : '');

        const img = document.createElement('img');
        img.src = dataUrl;
        img.draggable = false;
        item.appendChild(img);

        // 临时贴纸标识
        if (temporary) {
            const badge = document.createElement('span');
            badge.className = 'sticker-temp-badge';
            badge.textContent = t('cardExport.tempBadge', '临时');
            item.appendChild(badge);
        }

        // 右上角删除按钮
        const delBtn = document.createElement('button');
        delBtn.className = 'sticker-delete-btn';
        delBtn.innerHTML = '&times;';
        delBtn.title = t('cardExport.removeCustomSticker', '删除贴纸');
        delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            item.remove();
            if (!temporary) saveCustomStickers();
        });
        item.appendChild(delBtn);

        item.addEventListener('click', () => addSticker(dataUrl));

        // 插入到"+"按钮前面
        grid.insertBefore(item, importBtn);
        if (save && !temporary) saveCustomStickers();
    }

    // ====== IndexedDB 贴纸存储 ======
    const STICKER_DB_NAME = 'neko_stickers_db';
    const STICKER_DB_VERSION = 1;
    const STICKER_STORE_NAME = 'custom_stickers';

    function openStickerDB() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(STICKER_DB_NAME, STICKER_DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains(STICKER_STORE_NAME)) {
                    db.createObjectStore(STICKER_STORE_NAME, { keyPath: 'id', autoIncrement: true });
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    async function saveCustomStickers() {
        try {
            const items = document.querySelectorAll('.sticker-custom:not(.sticker-temporary) img');
            const urls = Array.from(items).map(img => img.src);
            const db = await openStickerDB();
            const tx = db.transaction(STICKER_STORE_NAME, 'readwrite');
            const store = tx.objectStore(STICKER_STORE_NAME);
            store.clear();
            urls.forEach(url => store.add({ data: url }));
            await new Promise((resolve, reject) => {
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
            });
        } catch (e) {
            console.warn('[CardExport] 保存自定义贴纸失败:', e);
        }
    }

    async function loadCustomStickers() {
        try {
            // 从旧 localStorage 迁移到 IndexedDB
            const legacy = localStorage.getItem('neko_custom_stickers');
            if (legacy) {
                const legacyUrls = JSON.parse(legacy);
                if (Array.isArray(legacyUrls) && legacyUrls.length > 0) {
                    const db = await openStickerDB();
                    const tx = db.transaction(STICKER_STORE_NAME, 'readwrite');
                    const store = tx.objectStore(STICKER_STORE_NAME);
                    legacyUrls.forEach(url => store.add({ data: url }));
                    await new Promise((resolve, reject) => {
                        tx.oncomplete = resolve;
                        tx.onerror = () => reject(tx.error);
                    });
                }
                localStorage.removeItem('neko_custom_stickers');
            }

            const db = await openStickerDB();
            const tx = db.transaction(STICKER_STORE_NAME, 'readonly');
            const store = tx.objectStore(STICKER_STORE_NAME);
            const req = store.getAll();
            const rows = await new Promise((resolve, reject) => {
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            });
            if (Array.isArray(rows)) {
                rows.forEach(row => addCustomStickerToGrid(row.data, false));
            }
        } catch (e) {
            console.warn('[CardExport] 加载自定义贴纸失败:', e);
        }
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
        refreshLayerPanel();

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
        if (s.id === selectedStickerId) updateRotateHandle(s);
    }

    /** 更新旋转手柄位置 */
    function updateRotateHandle(s) {
        const handle = $('#sticker-rotate-handle');
        if (!handle) return;
        if (!s) {
            handle.classList.remove('visible');
            return;
        }
        handle.classList.add('visible');
        // 手柄定位到贴纸左上角 (考虑旋转)
        const rad = s.rotation * Math.PI / 180;
        const halfW = s.w / 2, halfH = s.h / 2;
        // 左上角相对贴纸中心偏移 (-halfW, -halfH)，旋转后
        const rx = -halfW * Math.cos(rad) - (-halfH) * Math.sin(rad);
        const ry = -halfW * Math.sin(rad) + (-halfH) * Math.cos(rad);
        handle.style.left = `calc(${s.x}% + ${rx - 10}px)`;
        handle.style.top = `calc(${s.y}% + ${ry - 10}px)`;
    }

    /** 设置旋转手柄拖拽 */
    function setupRotateHandle() {
        const handle = $('#sticker-rotate-handle');
        if (!handle) return;

        let rotating = false;

        handle.addEventListener('pointerdown', (e) => {
            const s = getSelectedSticker();
            if (!s) return;
            e.preventDefault();
            e.stopPropagation();
            rotating = true;
            handle.setPointerCapture(e.pointerId);
        });

        handle.addEventListener('pointermove', (e) => {
            if (!rotating) return;
            e.stopPropagation();
            const s = getSelectedSticker();
            if (!s) return;
            const area = $('#card-portrait-area');
            const rect = area.getBoundingClientRect();
            // 贴纸中心在视口中的位置
            const cx = rect.left + (s.x / 100) * rect.width;
            const cy = rect.top + (s.y / 100) * rect.height;
            const angle = Math.atan2(e.clientY - cy, e.clientX - cx) * 180 / Math.PI;
            // 左上角自然角度是 -135°，偏移使手柄角度对应旋转 0°
            s.rotation = Math.round(angle + 135);
            // 归一化到 -180 ~ 180
            while (s.rotation > 180) s.rotation -= 360;
            while (s.rotation < -180) s.rotation += 360;
            updateStickerElement(s);
            // 同步滑块
            const rotInput = $('#sticker-rotation');
            const rotVal = $('#sticker-rotation-val');
            if (rotInput) rotInput.value = s.rotation;
            if (rotVal) rotVal.textContent = s.rotation + '°';
        });

        const stop = () => { rotating = false; };
        handle.addEventListener('pointerup', stop);
        handle.addEventListener('pointercancel', stop);
    }

    /** 同步贴纸尺寸到右侧滑块/数值框（模块级） */
    function _syncStickerSizeUI(s) {
        const wr = $('#sticker-w'), wv = $('#sticker-w-val');
        const hr = $('#sticker-h'), hv = $('#sticker-h-val');
        if (wr) wr.value = Math.min(s.w, 2000);
        if (wv) wv.textContent = s.w + 'px';
        if (hr) hr.value = Math.min(s.h, 2000);
        if (hv) hv.textContent = s.h + 'px';
    }

    /** 根据当前活动标签页切换贴纸的可交互性 */
    function updateStickerInteractivity() {
        const enabled = (activeTab === 'decor-tab');
        document.querySelectorAll('.sticker-placed').forEach(el => {
            el.style.pointerEvents = enabled ? 'auto' : 'none';
        });
        // 模型模式显示拖拽光标，装饰模式显示默认光标
        const preview = $('#card-preview');
        if (preview) {
            preview.style.cursor = (activeTab === 'model-tab') ? 'grab' : 'default';
        }
        // 非装饰模式隐藏旋转手柄
        if (!enabled) {
            updateRotateHandle(null);
            modelLayerSelected = false;
            const area = $('#card-portrait-area');
            if (area) area.classList.remove('model-focused');
        }
    }

    function setupStickerDrag(sticker, el) {
        let dragging = false;
        let startX, startY, startPctX, startPctY;

        el.addEventListener('pointerdown', (e) => {
            if (activeTab !== 'decor-tab') return;
            if (modelLayerSelected) return;
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
        if (id != null) {
            modelLayerSelected = false;
            const area = $('#card-portrait-area');
            if (area) area.classList.remove('model-focused');
        }
        // 更新视觉选中状态
        document.querySelectorAll('.sticker-placed').forEach(el => {
            el.classList.toggle('selected', Number(el.dataset.stickerId) === id);
        });

        const s = getSelectedSticker();
        const controls = $('#sticker-controls');
        if (s && controls) {
            controls.style.display = '';
            // 同步宽高 UI
            const wr = $('#sticker-w'), wv = $('#sticker-w-val');
            const hr = $('#sticker-h'), hv = $('#sticker-h-val');
            if (wr) wr.value = Math.min(s.w, 2000);
            if (wv) wv.textContent = s.w + 'px';
            if (hr) hr.value = Math.min(s.h, 2000);
            if (hv) hv.textContent = s.h + 'px';
            $('#sticker-rotation').value = s.rotation;
            $('#sticker-rotation-val').textContent = s.rotation + '°';
            updateRotateHandle(s);
        } else if (controls) {
            controls.style.display = 'none';
            updateRotateHandle(null);
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
        // 按 layerOrder 顺序排列贴纸到对应容器
        const ordered = layerOrder
            .filter(e => e.type === 'sticker')
            .map(e => stickers.find(s => s.id === e.id))
            .filter(Boolean);
        // 补上不在 layerOrder 中的贴纸（安全兜底）
        stickers.forEach(s => { if (!ordered.includes(s)) ordered.push(s); });
        ordered.forEach(s => {
            const target = (s.layer === 'below') ? below : above;
            target.appendChild(s.imgEl);
        });
    }

    function getSelectedSticker() {
        return stickers.find(s => s.id === selectedStickerId) || null;
    }

    function removeStickerById(id) {
        const idx = stickers.findIndex(s => s.id === id);
        if (idx === -1) return;
        stickers[idx].imgEl.remove();
        stickers.splice(idx, 1);
        if (selectedStickerId === id) {
            selectedStickerId = null;
            selectSticker(null);
        }
        updateStickerOverlayOrder();
        refreshLayerPanel();
    }

    function removeSelectedSticker() {
        if (selectedStickerId == null) return;
        removeStickerById(selectedStickerId);
    }

    function clearAllStickers() {
        stickers.forEach(s => s.imgEl.remove());
        stickers.length = 0;
        selectedStickerId = null;
        selectSticker(null);
        refreshLayerPanel();
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
    function t(key, fallback, options) {
        if (window.i18next && typeof window.i18next.t === 'function') {
            const val = window.i18next.t(key, options);
            if (val && val !== key) return val;
        }
        if (window.t && typeof window.t === 'function') {
            const val = window.t(key, options);
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
    // ====== 图层列表面板 ======

    // 内部有序列表：从上到下排列的项目（贴纸或 'model' 哨兵）
    // 初始只有 model，贴纸添加时插入到 model 前面（above）
    const layerOrder = [{ type: 'model' }];

    /**
     * 刷新图层面板 UI
     */
    function refreshLayerPanel() {
        const panel = $('#layer-panel');
        const list = $('#layer-list');
        if (!panel || !list) return;

        if (activeTab !== 'decor-tab' || stickers.length === 0) {
            panel.classList.remove('visible');
            return;
        }
        panel.classList.add('visible');
        list.innerHTML = '';

        // 同步 layerOrder：移除已删除的贴纸，添加新贴纸
        syncLayerOrder();

        layerOrder.forEach((entry, idx) => {
            if (entry.type === 'model') {
                list.appendChild(createModelLayerItem(idx));
            } else {
                const s = stickers.find(st => st.id === entry.id);
                if (s) list.appendChild(createStickerLayerItem(s, idx));
            }
        });

        setupLayerDrag();
    }

    /** 保持 layerOrder 与 stickers 数组同步 */
    function syncLayerOrder() {
        // 移除已不存在的贴纸
        for (let i = layerOrder.length - 1; i >= 0; i--) {
            if (layerOrder[i].type === 'sticker') {
                if (!stickers.find(s => s.id === layerOrder[i].id)) {
                    layerOrder.splice(i, 1);
                }
            }
        }
        // 添加不在 layerOrder 中的新贴纸（默认插到模型上方）
        const modelIdx = layerOrder.findIndex(e => e.type === 'model');
        stickers.forEach(s => {
            if (!layerOrder.find(e => e.type === 'sticker' && e.id === s.id)) {
                layerOrder.splice(modelIdx, 0, { type: 'sticker', id: s.id });
            }
        });
    }

    /** 根据 layerOrder 更新所有贴纸的 layer 属性和 DOM */
    function applyLayerOrderToStickers() {
        const modelIdx = layerOrder.findIndex(e => e.type === 'model');
        layerOrder.forEach((entry, idx) => {
            if (entry.type !== 'sticker') return;
            const s = stickers.find(st => st.id === entry.id);
            if (!s) return;
            s.layer = (idx < modelIdx) ? 'above' : 'below';
        });
        updateStickerOverlayOrder();
    }

    function createModelLayerItem(orderIdx) {
        const item = document.createElement('div');
        item.className = 'layer-item is-model' + (modelLayerSelected ? ' selected' : '');
        item.dataset.layerIdx = orderIdx;
        item.draggable = true;
        item.innerHTML = `<span class="layer-item-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="10" r="3"/><path d="M6 21v-1a6 6 0 0112 0v1"/></svg></span><span class="layer-item-name">${t('cardExport.modelLayer', '模型')}</span><span class="layer-drag-handle">⠿</span>`;

        item.addEventListener('click', () => {
            modelLayerSelected = true;
            selectedStickerId = null;
            // 取消贴纸选中状态
            document.querySelectorAll('.sticker-placed').forEach(el => el.classList.remove('selected'));
            updateRotateHandle(null);
            const controls = $('#sticker-controls');
            if (controls) controls.style.display = 'none';
            // 标记模型聚焦，禁用贴纸交互
            const area = $('#card-portrait-area');
            if (area) area.classList.add('model-focused');
            refreshLayerPanel();
        });

        return item;
    }

    function createStickerLayerItem(s, orderIdx) {
        const item = document.createElement('div');
        item.className = 'layer-item' + (s.id === selectedStickerId ? ' selected' : '');
        item.dataset.stickerId = s.id;
        item.dataset.layerIdx = orderIdx;
        item.draggable = true;

        const thumb = document.createElement('img');
        thumb.className = 'layer-item-thumb';
        thumb.src = s.src;
        thumb.draggable = false;

        const name = document.createElement('span');
        name.className = 'layer-item-name';
        name.textContent = t('cardExport.sticker', '贴纸') + ' #' + s.id;

        const delBtn = document.createElement('span');
        delBtn.className = 'layer-delete-btn';
        delBtn.title = t('cardExport.removeSticker', '删除选中');
        delBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
        delBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeStickerById(s.id);
        });

        const handle = document.createElement('span');
        handle.className = 'layer-drag-handle';
        handle.textContent = '⠿';

        item.appendChild(thumb);
        item.appendChild(name);
        item.appendChild(delBtn);
        item.appendChild(handle);

        item.addEventListener('click', () => {
            selectSticker(s.id);
            refreshLayerPanel();
        });

        return item;
    }

    function setupLayerDrag() {
        const list = $('#layer-list');
        if (!list) return;

        let dragItem = null;
        let dropPosition = 'before'; // 'before' or 'after'

        function clearIndicators() {
            list.querySelectorAll('.layer-item').forEach(el => {
                el.classList.remove('drag-over-top', 'drag-over-bottom');
            });
        }

        list.querySelectorAll('.layer-item').forEach(item => {
            item.addEventListener('dragstart', (e) => {
                dragItem = item;
                item.style.opacity = '0.4';
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', item.dataset.layerIdx);
            });

            item.addEventListener('dragend', () => {
                item.style.opacity = '';
                dragItem = null;
                clearIndicators();
            });

            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (item === dragItem) return;

                clearIndicators();
                const rect = item.getBoundingClientRect();
                const midY = rect.top + rect.height / 2;
                if (e.clientY < midY) {
                    item.classList.add('drag-over-top');
                    dropPosition = 'before';
                } else {
                    item.classList.add('drag-over-bottom');
                    dropPosition = 'after';
                }
            });

            item.addEventListener('dragleave', () => {
                item.classList.remove('drag-over-top', 'drag-over-bottom');
            });

            item.addEventListener('drop', (e) => {
                e.preventDefault();
                clearIndicators();
                if (!dragItem || dragItem === item) return;

                const fromIdx = Number(dragItem.dataset.layerIdx);
                let toIdx = Number(item.dataset.layerIdx);
                if (isNaN(fromIdx) || isNaN(toIdx)) return;

                const [moved] = layerOrder.splice(fromIdx, 1);
                // 移除后索引可能偏移，重新计算目标位置
                if (fromIdx < toIdx) toIdx--;
                const insertIdx = dropPosition === 'after' ? toIdx + 1 : toIdx;
                layerOrder.splice(insertIdx, 0, moved);

                applyLayerOrderToStickers();
                refreshLayerPanel();
            });
        });
    }
})();
