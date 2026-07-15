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
    let currentModelType = '';   // 'live2d' | 'vrm' | 'mmd' | 'pngtuber'
    let isModelLoaded = false;
    let isModelLoading = false;
    let primaryActionBusy = false;
    const MODEL_LOADING_CLOSE_FALLBACK_MS = 8000;
    let modelLoadingStartedAt = 0;
    let allowCloseWhileLoading = false;
    let loadingCloseFallbackTimer = null;
    let previewLoopId = null;     // requestAnimationFrame ID
    let lastPreviewTime = 0;      // 上次预览渲染时间戳

    // 构图参数
    const composition = { offsetX: 0, offsetY: 0, scale: 100, rotation: 0 };
    const MODEL_OFFSET_X_MIN = -800;
    const MODEL_OFFSET_X_MAX = 800;
    const MODEL_OFFSET_Y_MIN = -1000;
    const MODEL_OFFSET_Y_MAX = 1000;
    const MODEL_SCALE_MIN = 50;
    const MODEL_SCALE_MAX = 600;

    // 卡面以 3:4 输出。UI 仍按 CSS 尺寸显示，内部使用更高像素密度避免把模型截图放大后发糊。
    const CARD_BASE_WIDTH = 600;
    const CARD_BASE_HEIGHT = 800;
    const CARD_OUTPUT_SCALE = 2;       // 保存/导出 1200×1600
    const MODEL_PREVIEW_SOURCE_SCALE = 2; // 实时预览源画布 1200×1600，保证流畅
    const MODEL_EXPORT_SOURCE_SCALE = 3;  // 保存/导出时临时升到 1800×2400
    const MODEL_PREVIEW_MAX_SOURCE_SCALE = 5;
    const MODEL_EXPORT_MAX_SOURCE_SCALE = 8;
    const PREVIEW_MIN_PIXEL_RATIO = 2;
    const PREVIEW_TARGET_FPS = 60;
    const PREVIEW_FRAME_INTERVAL_MS = 1000 / PREVIEW_TARGET_FPS;
    let activeModelSourceScale = MODEL_PREVIEW_SOURCE_SCALE;

    window.renderQuality = 'high';

    // 贴纸状态
    const stickers = [];           // { id, src, x, y, w, h, rotation, layer, imgEl }
    let stickerIdCounter = 0;
    let selectedStickerId = null;
    let modelLayerSelected = false;  // 图层面板中模型是否被选中

    // 当前激活的标签页: 'model-tab' | 'decor-tab'
    let activeTab = 'model-tab';

    // 可用贴纸列表；带形态数组的条目会在列表和已放置贴纸上提供形态切换。
    const STICKER_LIBRARY_ITEMS = [
        'add.png', 'angry_cat.png', 'calm_cat.png', 'cat_icon.png',
        'character_icon.png', 'chat_bubble.png', 'chat_icon.png',
        'default_character_card.png', 'emotion_model_icon.png',
        'exclamation.png', 'happy_cat.png', 'icon_systray.ico',
        'paw_ui.png', 'reminder_icon.png', 'sad_cat.png',
        'send_icon.png', 'send_new_icon.png', 'surprise_cat.png',
        { file: 'chat_sugar1.png', variants: ['chat_sugar1.png', 'chat_sugar3.png'] },
        { file: 'chat_hammer1.png', variants: ['chat_hammer1.png', 'chat_hammer2.png'] },
        'cat_moneny.png',
        { file: 'cat_claw1.png', variants: ['cat_claw1.png', 'cat_claw2.png'] }
    ];
    const STICKER_VARIANT_GROUPS = [
        ['chat_sugar1.png', 'chat_sugar3.png'],
        ['chat_hammer1.png', 'chat_hammer2.png'],
        ['cat_claw1.png', 'cat_claw2.png']
    ].map(group => group.map(file => `/static/icons/${file}`));

    const STICKER_VARIANT_ICON = [
        '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">',
        '<polyline points="17 1 21 5 17 9"/>',
        '<path d="M3 11V9a4 4 0 0 1 4-4h14"/>',
        '<polyline points="7 23 3 19 7 15"/>',
        '<path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
        '</svg>'
    ].join('');

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
    const autoSaveDefaultCardFace = _urlParams.get('auto_save_default') === '1';
    const closeAfterAutoSave = _urlParams.get('close_on_save') === '1';
    const fallbackDefaultOnClose = _urlParams.get('fallback_default_on_close') === '1';
    const fallbackToken = _urlParams.get('fallback_token') || '';
    const fallbackPageName = _urlParams.get('name') || _urlParams.get('lanlan_name') || '';
    let cardFaceSaved = false;
    let fallbackDefaultSaving = false;
    let fallbackDefaultPromise = null;
    let fallbackEventChannel = null;
    let pendingFallbackDefaultSave = false;
    let fallbackDefaultListenersRegistered = false;

    initModelSaveFallbackDefaultCardFace();

    // ====== 初始化 ======
    document.addEventListener('DOMContentLoaded', async () => {
        // 禁用鼠标跟踪（导出页面不需要）
        window.mouseTrackingEnabled = false;
        showLoading(true);

        // 设置标题和按钮（maker 模式与导出模式使用不同文案）
        syncCompositionControlLimits();
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
            if (consumeFallbackCloseMark(name)) {
                pendingFallbackDefaultSave = true;
            }
            if (pendingFallbackDefaultSave && !autoSaveDefaultCardFace) {
                await saveModelSaveFallbackDefaultCardFace('pending-owner-close');
            }
            if (autoSaveDefaultCardFace) {
                pendingFallbackDefaultSave = false;
                await doAutoSaveDefaultCardFace();
            }
        } else {
            showLoading(false);
        }
    });

    // ====== 事件绑定 ======
    function bindEvents() {
        // 构图滑块（实时预览由循环驱动，滑块仅更新参数）
        offsetXInput.addEventListener('input', () => {
            composition.offsetX = clamp(Number(offsetXInput.value), MODEL_OFFSET_X_MIN, MODEL_OFFSET_X_MAX);
            offsetXInput.value = composition.offsetX;
            offsetXVal.textContent = composition.offsetX;
        });
        offsetYInput.addEventListener('input', () => {
            composition.offsetY = clamp(Number(offsetYInput.value), MODEL_OFFSET_Y_MIN, MODEL_OFFSET_Y_MAX);
            offsetYInput.value = composition.offsetY;
            offsetYVal.textContent = composition.offsetY;
        });
        scaleInput.addEventListener('input', () => {
            composition.scale = clamp(Number(scaleInput.value), MODEL_SCALE_MIN, MODEL_SCALE_MAX);
            scaleInput.value = composition.scale;
            scaleVal.textContent = composition.scale + '%';
            updatePreviewSourceScaleForZoom();
        });
        rotationInput.addEventListener('input', () => {
            composition.rotation = Number(rotationInput.value);
            rotationVal.textContent = composition.rotation + '°';
        });

        resetBtn.addEventListener('click', resetComposition);
        refreshBtn.addEventListener('click', () => refreshPreview());
        exportFullBtn.addEventListener('click', () => {
            if (isMakerMode) {
                doSaveCardFace().catch(() => {});
            }
            else { doExport('full'); }
        });
        backBtn.addEventListener('click', () => {
            closeCardMakerPage();
        });
        window.nekoBeforeWindowClose = async () => {
            const handled = await closeCardMakerPage();
            return handled ? { handled: true } : undefined;
        };

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
        const switchVariantBtn = $('#sticker-switch-variant-btn');
        if (switchVariantBtn) {
            switchVariantBtn.addEventListener('click', () => switchSelectedStickerVariant());
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

    async function closeCardMakerPage() {
        if (isModelLoading && !canCloseWhileLoading()) return false;
        if (isModelLoading) {
            closeCardMakerWindow();
            return true;
        }
        try {
            await saveModelSaveFallbackDefaultCardFace('card-maker-close', {
                maxWait: 1200,
                skipIfModelNotLoaded: true,
                waitForExisting: false
            });
        } catch (error) {
            console.error('[CardMaker] 关闭前默认卡面兜底失败:', error);
        }
        closeCardMakerWindow();
        return true;
    }

    function closeCardMakerWindow() {
        if (window.opener) { window.close(); }
        else { window.history.back(); }
    }

    function shouldSaveFallbackDefaultCardFace() {
        return isMakerMode &&
            fallbackDefaultOnClose &&
            !!currentCharaName &&
            !cardFaceSaved &&
            !autoSaveDefaultCardFace;
    }

    function matchesModelSaveFallbackEvent(data) {
        if (!fallbackDefaultOnClose || !data ||
            data.type !== 'model-manager-card-maker-fallback-owner-closing') {
            return false;
        }
        if (fallbackToken && data.token !== fallbackToken) {
            return false;
        }
        if (!fallbackToken) {
            const eventName = String(data.name || '').trim();
            const expectedName = String(currentCharaName || fallbackPageName || '').trim();
            return !!eventName && !!expectedName && eventName === expectedName;
        }
        return !currentCharaName || !data.name || data.name === currentCharaName;
    }

    function getFallbackCloseMarkKey(name = '') {
        const roleName = String(name || currentCharaName || fallbackPageName || '').trim();
        if (!fallbackDefaultOnClose || !fallbackToken || !roleName) return '';
        try {
            return `neko_card_maker_fallback_closed:${encodeURIComponent(fallbackToken)}:${encodeURIComponent(roleName)}`;
        } catch (_) {
            return '';
        }
    }

    function persistFallbackCloseMark(data) {
        const key = getFallbackCloseMarkKey(data?.name);
        if (!key) return;
        try {
            localStorage.setItem(key, JSON.stringify({
                token: fallbackToken,
                name: String(data?.name || '').trim(),
                timestamp: Date.now()
            }));
        } catch (_) {}
    }

    function consumeFallbackCloseMark(name = '') {
        const key = getFallbackCloseMarkKey(name);
        if (!key) return false;
        try {
            const value = localStorage.getItem(key);
            if (!value) return false;
            localStorage.removeItem(key);
            return true;
        } catch (_) {
            return false;
        }
    }

    async function saveModelSaveFallbackDefaultCardFace(reason = '', options = {}) {
        if (fallbackDefaultSaving) {
            return options.waitForExisting === false ? false : (fallbackDefaultPromise || false);
        }
        if (!shouldSaveFallbackDefaultCardFace()) {
            if (!currentCharaName && fallbackDefaultOnClose) {
                pendingFallbackDefaultSave = true;
            }
            return false;
        }
        if (options.skipIfModelNotLoaded && !isModelLoaded) {
            return false;
        }

        const maxWait = Number.isFinite(options.maxWait) && options.maxWait >= 0
            ? options.maxWait
            : 10000;
        fallbackDefaultSaving = true;
        fallbackDefaultPromise = (async () => {
            await waitForCondition(() => isModelLoaded, maxWait, '模型加载');
            await new Promise(resolve => setTimeout(resolve, 200));
            const saveResult = await doSaveCardFace({
                silent: true,
                statusText: t('cardExport.autoSavingDefaultCardFace', '正在生成默认卡面...'),
                renderOptions: {
                    includeStickers: false,
                    composition: { offsetX: 0, offsetY: 0, scale: 100, rotation: 0 }
                }
            });
            if (saveResult?.status === 'partial') {
                console.warn('[CardMaker] 模型保存流程默认卡面兜底部分成功:', reason || 'unknown');
            } else if (saveResult?.status === 'ok') {
                console.log('[CardMaker] 已生成模型保存流程的默认卡面兜底:', reason || 'unknown');
            }
            const saveSucceeded = saveResult?.status === 'ok' || saveResult?.status === 'partial';
            if (saveSucceeded) {
                consumeFallbackCloseMark(currentCharaName);
                pendingFallbackDefaultSave = false;
            }
            return saveSucceeded;
        })();

        try {
            return await fallbackDefaultPromise;
        } catch (error) {
            console.error('[CardMaker] 模型保存流程默认卡面兜底失败:', error);
            return false;
        } finally {
            fallbackDefaultSaving = false;
            fallbackDefaultPromise = null;
        }
    }

    function initModelSaveFallbackDefaultCardFace() {
        if (!fallbackDefaultOnClose || fallbackDefaultListenersRegistered) return;
        fallbackDefaultListenersRegistered = true;

        const handleFallbackEvent = (data) => {
            if (!matchesModelSaveFallbackEvent(data)) return;
            persistFallbackCloseMark(data);
            pendingFallbackDefaultSave = true;
            saveModelSaveFallbackDefaultCardFace('model-manager-close').catch(() => {});
        };

        window.addEventListener('message', event => {
            if (event.origin !== window.location.origin) return;
            handleFallbackEvent(event.data);
        });

        if (typeof BroadcastChannel === 'function') {
            try {
                fallbackEventChannel = new BroadcastChannel('neko-card-maker-fallback-events');
                fallbackEventChannel.onmessage = event => handleFallbackEvent(event.data);
            } catch (_) {}
        }

        window.addEventListener('storage', event => {
            if (event.key !== 'neko_card_maker_fallback_event' || !event.newValue) return;
            try {
                handleFallbackEvent(JSON.parse(event.newValue));
            } catch (_) {}
        });

        const cleanupFallbackChannel = () => {
            if (!fallbackEventChannel) return;
            try {
                fallbackEventChannel.onmessage = null;
                fallbackEventChannel.close();
            } catch (_) {}
            fallbackEventChannel = null;
        };
        window.addEventListener('pagehide', cleanupFallbackChannel, { once: true });
        window.addEventListener('beforeunload', cleanupFallbackChannel, { once: true });
    }

    // ====== 角色加载 ======
    async function onCharacterSelected(name) {
        if (!name) return;
        currentCharaName = name;

        isModelLoaded = false;
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
            if (cfg.model_type === 'pngtuber') {
                window.lanlan_config.pngtuber = Object.assign({}, cfg.pngtuber || {});
            }
            if (cfg.model_type === 'live3d') {
                window.lanlan_config.live3d_sub_type = cfg.live3d_sub_type;
            }

            // 确定实际模型类型
            let effectiveType = 'live2d';
            if (cfg.model_type === 'live3d') {
                effectiveType = (cfg.live3d_sub_type === 'mmd') ? 'mmd' : 'vrm';
            } else if (cfg.model_type === 'vrm') {
                effectiveType = 'vrm';
            } else if (cfg.model_type === 'pngtuber') {
                effectiveType = 'pngtuber';
            }
            currentModelType = effectiveType;

            await loadCharacterModel(effectiveType, cfg);
        } catch (e) {
            console.error('[CardExport] 加载角色模型失败:', e);
            showLoading(false);
            updatePrimaryActionAvailability();
        }
    }

    // ====== 模型加载 ======
    async function loadCharacterModel(type, cfg) {
        isModelLoaded = false;
        stopPreviewLoop();
        prepareHiddenModelViewport();

        // 先隐藏所有渲染容器
        const l2dContainer = $('#live2d-container');
        const vrmContainer = $('#vrm-container');
        const mmdContainer = $('#mmd-container');
        const pngtuberContainer = $('#pngtuber-container');
        l2dContainer.style.display = 'none';
        vrmContainer.style.display = 'none';
        mmdContainer.style.display = 'none';
        if (pngtuberContainer) pngtuberContainer.style.display = 'none';
        if (type !== 'pngtuber') {
            window.cardMakerPNGTuberManager?.hide?.();
        }

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
            } else if (type === 'pngtuber') {
                if (pngtuberContainer) pngtuberContainer.style.display = '';
                await loadPNGTuberModel(cfg);
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
            updatePrimaryActionAvailability();
        }
    }

    async function loadLive2DModel(modelPath) {
        if (!window.live2dManager) {
            throw new Error('Live2D 管理器未就绪');
        }
        // 初始化 PIXI（如果尚未初始化），启用 preserveDrawingBuffer 以便截图
        if (!window.live2dManager.pixi_app) {
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container', {
                preserveDrawingBuffer: true,
                resolution: MODEL_PREVIEW_SOURCE_SCALE,
                autoDensity: true
            });
        }
        resizeModelRendererForCard('live2d');
        await window.live2dManager.loadModel(modelPath);

        // 将模型居中（默认布局放在右下角，导出页面需要居中）
        const model = window.live2dManager.currentModel;
        if (model) {
            const screen = window.live2dManager.pixi_app.renderer.screen;
            model.anchor.set(0.5, 0.5);
            model.x = screen.width / 2;
            model.y = screen.height / 2;
        }
        resizeModelRendererForCard('live2d');
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
        resizeModelRendererForCard('vrm');
        if (lighting) {
            window.lanlan_config.lighting = lighting;
        }
        await window.vrmManager.loadModel(modelPath);
        // 重置相机：让模型居中填满画布（忽略主页面保存的相机位置）
        resizeModelRendererForCard('vrm');
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
        resizeModelRendererForCard('mmd');
        await window.mmdManager.loadModel(modelPath);
        // 重置相机：让模型居中填满画布
        const mmdProxy = {
            scene: window.mmdManager.core?.scene,
            camera: window.mmdManager.core?.camera,
            renderer: window.mmdManager.core?.renderer
        };
        resizeModelRendererForCard('mmd');
        centerThreeCamera(mmdProxy);
    }

    async function loadPNGTuberModel(cfg) {
        await waitForCondition(() => typeof window.PNGTuberManager === 'function', 10000, 'PNGTuber runtime');

        const pngtuberConfig = Object.assign({}, cfg?.pngtuber || {});
        if (!pngtuberConfig.idle_image && cfg?.model_path) {
            pngtuberConfig.idle_image = cfg.model_path;
        }
        assertExportablePNGTuberConfig(pngtuberConfig);
        window.lanlan_config = window.lanlan_config || {};
        window.lanlan_config.model_type = 'pngtuber';
        window.lanlan_config.pngtuber = Object.assign({}, pngtuberConfig);

        if (!window.cardMakerPNGTuberManager) {
            window.cardMakerPNGTuberManager = new window.PNGTuberManager('pngtuber-container');
        }
        const mgr = window.cardMakerPNGTuberManager;
        const originalSetupFloatingButtons = mgr.setupFloatingButtons;
        const originalSetupHTMLLockIcon = mgr.setupHTMLLockIcon;
        mgr.setupFloatingButtons = undefined;
        mgr.setupHTMLLockIcon = function() {};
        try {
            await mgr.load(pngtuberConfig);
        } finally {
            if (originalSetupFloatingButtons) {
                mgr.setupFloatingButtons = originalSetupFloatingButtons;
            } else {
                delete mgr.setupFloatingButtons;
            }
            if (originalSetupHTMLLockIcon) {
                mgr.setupHTMLLockIcon = originalSetupHTMLLockIcon;
            } else {
                delete mgr.setupHTMLLockIcon;
            }
        }
        mgr.detachSpeechListeners?.();
        mgr.detachDragListeners?.();
        mgr.detachLayeredHotkeys?.();
        mgr.detachLayeredPlayEvent?.();
        mgr.cleanupFloatingButtons?.();
        removePNGTuberRuntimeControls();
        mgr.setSpeaking?.(false);
        if (typeof mgr.setLayeredStateIndex === 'function') {
            mgr.setLayeredStateIndex(0, { source: 'card_maker' });
        }
        mgr.setState?.('idle');
        mgr.show?.();
        mgr.clearLayeredTimers?.();
        mgr.detachLayeredHotkeys?.();
        mgr.detachLayeredPlayEvent?.();
        if (mgr.isLayeredActive?.()) {
            mgr.drawLayeredState?.('idle');
        }
        resizeModelRendererForCard('pngtuber');
        await waitForPNGTuberDrawable(mgr);
    }

    function prepareHiddenModelViewport() {
        const viewport = $('#model-viewport');
        if (!viewport) return;
        viewport.style.inset = 'auto';
        viewport.style.left = '-10000px';
        viewport.style.top = '0';
        viewport.style.width = CARD_BASE_WIDTH + 'px';
        viewport.style.height = CARD_BASE_HEIGHT + 'px';
    }

    function resizeModelRendererForCard(type = currentModelType, sourceScale = MODEL_PREVIEW_SOURCE_SCALE) {
        const w = CARD_BASE_WIDTH;
        const h = CARD_BASE_HEIGHT;
        const ratio = sourceScale;
        activeModelSourceScale = sourceScale;

        if (type === 'live2d') {
            const mgr = window.live2dManager;
            const renderer = mgr?.pixi_app?.renderer;
            if (!renderer) return;
            renderer.resolution = ratio;
            renderer.resize(w, h);
            const view = renderer.view || document.getElementById('live2d-canvas');
            if (view) {
                view.style.width = w + 'px';
                view.style.height = h + 'px';
            }
            const model = mgr.currentModel;
            if (model) {
                model.anchor?.set?.(0.5, 0.5);
                model.x = renderer.screen.width / 2;
                model.y = renderer.screen.height / 2;
            }
            return;
        }

        if (type === 'pngtuber') {
            const container = document.getElementById('pngtuber-container');
            if (container) {
                container.style.width = w + 'px';
                container.style.height = h + 'px';
            }
            const mgr = window.cardMakerPNGTuberManager;
            const source = getPNGTuberDrawableSource(mgr);
            if (source?.style) {
                source.style.width = w + 'px';
                source.style.height = h + 'px';
                source.style.objectFit = 'contain';
            }
            return;
        }

        const mgr = type === 'vrm'
            ? window.vrmManager
            : {
                renderer: window.mmdManager?.core?.renderer,
                camera: window.mmdManager?.core?.camera,
                effect: window.mmdManager?.core?.effect || window.mmdManager?.effect
            };
        const renderer = mgr?.renderer;
        if (!renderer) return;
        renderer.setPixelRatio?.(ratio);
        renderer.setSize?.(w, h, false);
        if (renderer.domElement) {
            renderer.domElement.style.width = w + 'px';
            renderer.domElement.style.height = h + 'px';
        }
        if (mgr.camera) {
            mgr.camera.aspect = w / h;
            mgr.camera.updateProjectionMatrix?.();
        }
        mgr.effect?.setSize?.(w, h);
    }

    function getZoomFactor(compositionOverride = composition) {
        const scale = Number(compositionOverride?.scale);
        return Math.max(1, Number.isFinite(scale) ? scale / 100 : 1);
    }

    function getPreviewSourceScaleForZoom() {
        return clamp(
            Math.ceil(MODEL_PREVIEW_SOURCE_SCALE * getZoomFactor()),
            MODEL_PREVIEW_SOURCE_SCALE,
            MODEL_PREVIEW_MAX_SOURCE_SCALE
        );
    }

    function getExportSourceScaleForZoom(compositionOverride = composition) {
        return clamp(
            Math.ceil(MODEL_EXPORT_SOURCE_SCALE * getZoomFactor(compositionOverride)),
            MODEL_EXPORT_SOURCE_SCALE,
            MODEL_EXPORT_MAX_SOURCE_SCALE
        );
    }

    function updatePreviewSourceScaleForZoom() {
        if (!isModelLoaded || !currentModelType) return;
        const nextScale = getPreviewSourceScaleForZoom();
        if (nextScale === activeModelSourceScale) return;
        resizeModelRendererForCard(currentModelType, nextScale);
        ensureRender();
        refreshPreview();
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
        const pngtuberMgr = window.cardMakerPNGTuberManager;
        pngtuberMgr?.setMouseTrackingEnabled?.(false);
        pngtuberMgr?.detachSpeechListeners?.();
        pngtuberMgr?.detachDragListeners?.();
        pngtuberMgr?.setSpeaking?.(false);
        removePNGTuberRuntimeControls();
    }

    function removePNGTuberRuntimeControls() {
        document.querySelectorAll('#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container')
            .forEach((el) => {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    function getDrawableSourceSize(source) {
        if (!source) return { width: 0, height: 0 };
        return {
            width: source.naturalWidth || source.videoWidth || source.width || 0,
            height: source.naturalHeight || source.videoHeight || source.height || 0
        };
    }

    function isCrossOriginHttpUrl(value) {
        if (!value || typeof value !== 'string') return false;
        try {
            const url = new URL(value, window.location.href);
            return /^https?:$/i.test(url.protocol) && url.origin !== window.location.origin;
        } catch (_) {
            return /^https?:\/\//i.test(value);
        }
    }

    function assertExportablePNGTuberConfig(config) {
        const imageKeys = ['idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'];
        const remoteKey = imageKeys.concat(['layered_metadata']).find((key) => isCrossOriginHttpUrl(config && config[key]));
        if (remoteKey) {
            throw new Error(`remote_pngtuber_export_unsupported:${remoteKey}`);
        }
    }

    function assertExportablePNGTuberDrawable(source) {
        if (!source) return;
        if (source.tagName === 'IMG' && isCrossOriginHttpUrl(source.currentSrc || source.src || source.getAttribute('src'))) {
            throw new Error('remote_pngtuber_export_unsupported:drawable');
        }
        if (source.tagName === 'CANVAS') {
            try {
                const ctx = source.getContext('2d');
                ctx?.getImageData(0, 0, 1, 1);
            } catch (_) {
                throw new Error('remote_pngtuber_export_unsupported:canvas');
            }
        }
    }

    function getPNGTuberDrawableSource(mgr = window.cardMakerPNGTuberManager) {
        if (mgr?.isLayeredActive?.() && mgr.canvasElement) {
            return mgr.canvasElement;
        }
        if (mgr?.imageElement) {
            return mgr.imageElement;
        }
        const container = document.getElementById('pngtuber-container');
        return container?.querySelector('canvas.pngtuber-layered-canvas, img.pngtuber-image') || null;
    }

    async function waitForImageReady(image) {
        if (!image || image.tagName !== 'IMG') return;
        if (image.complete && image.naturalWidth > 0 && image.naturalHeight > 0) return;
        await new Promise((resolve, reject) => {
            const cleanup = () => {
                image.removeEventListener('load', onLoad);
                image.removeEventListener('error', onError);
            };
            const onLoad = () => {
                cleanup();
                resolve();
            };
            const onError = () => {
                cleanup();
                reject(new Error('PNGTuber image failed to load'));
            };
            image.addEventListener('load', onLoad, { once: true });
            image.addEventListener('error', onError, { once: true });
        });
    }

    async function waitForPNGTuberDrawable(mgr) {
        const source = getPNGTuberDrawableSource(mgr);
        if (!source) throw new Error('PNGTuber drawable source is missing');
        if (source.tagName === 'IMG') {
            await waitForImageReady(source);
        } else if (mgr?.isLayeredActive?.()) {
            mgr.drawLayeredState?.('idle');
        }
        const size = getDrawableSourceSize(source);
        if (size.width <= 0 || size.height <= 0) {
            throw new Error('PNGTuber drawable source is empty');
        }
        assertExportablePNGTuberDrawable(source);
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
        if (currentModelType === 'pngtuber') {
            return getPNGTuberDrawableSource();
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
        } else if (currentModelType === 'pngtuber') {
            const mgr = window.cardMakerPNGTuberManager;
            mgr?.setSpeaking?.(false);
            if (typeof mgr?.setLayeredStateIndex === 'function' && mgr.layeredStateIndex !== 0) {
                mgr.setLayeredStateIndex(0, { source: 'card_maker' });
            }
            mgr?.setState?.('idle');
            mgr?.clearLayeredTimers?.();
            if (mgr?.isLayeredActive?.()) {
                mgr.drawLayeredState?.('idle');
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
    function drawModelWithComposition(ctx, srcCanvas, outW, outH, compositionOverride = composition) {
        // 从源画布中裁剪出 3:4 比例的区域（cover 语义）
        const dstAspect = outW / outH;           // ≈ 0.75 (3:4)
        const sourceSize = getDrawableSourceSize(srcCanvas);
        if (sourceSize.width <= 0 || sourceSize.height <= 0) return;
        const srcAspect = sourceSize.width / sourceSize.height;
        let sx = 0, sy = 0, sw = sourceSize.width, sh = sourceSize.height;

        if (srcAspect > dstAspect) {
            // 源更宽 → 裁两侧
            sw = sourceSize.height * dstAspect;
            sx = (sourceSize.width - sw) / 2;
        } else {
            // 源更高 → 裁上下
            sh = sourceSize.width / dstAspect;
            sy = (sourceSize.height - sh) / 2;
        }

        const activeComposition = compositionOverride;
        const scale = activeComposition.scale / 100;
        const drawW = outW * scale;
        const drawH = outH * scale;

        // 偏移量在 450×600 坐标系下定义，按实际尺寸等比缩放
        const ratio = outW / 450;
        const dx = (outW - drawW) / 2 + activeComposition.offsetX * ratio;
        const dy = (outH - drawH) / 2 + activeComposition.offsetY * ratio;

        // 应用旋转（围绕模型中心）
        const angle = activeComposition.rotation * Math.PI / 180;
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
     * 启动持续预览刷新（目标 60fps，用 requestAnimationFrame 对齐显示刷新）
     */
    function startPreviewLoop() {
        stopPreviewLoop();
        lastPreviewTime = 0;

        function loop(timestamp) {
            previewLoopId = requestAnimationFrame(loop);
            if (document.hidden) return;
            if (timestamp - lastPreviewTime < PREVIEW_FRAME_INTERVAL_MS) return;
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
        updatePreviewSourceScaleForZoom();

        const srcCanvas = getModelCanvas();
        const srcSize = getDrawableSourceSize(srcCanvas);
        if (!srcCanvas || srcSize.width <= 0 || srcSize.height <= 0) return;

        ensureRender();

        const ctx = portraitCanvas.getContext('2d');
        const areaEl = $('#card-portrait-area');
        const w = areaEl.clientWidth;
        const h = areaEl.clientHeight;
        if (w <= 0 || h <= 0) return;

        const dpr = Math.max(PREVIEW_MIN_PIXEL_RATIO, window.devicePixelRatio || 1);
        const needW = Math.round(w * dpr);
        const needH = Math.round(h * dpr);
        if (portraitCanvas.width !== needW || portraitCanvas.height !== needH) {
            portraitCanvas.width = needW;
            portraitCanvas.height = needH;
            portraitCanvas.style.width = w + 'px';
            portraitCanvas.style.height = h + 'px';
        }
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
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
            if (e.button !== 0) return;
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
            composition.offsetX = clamp(Math.round(startOX + (e.clientX - startX) / previewScale), MODEL_OFFSET_X_MIN, MODEL_OFFSET_X_MAX);
            composition.offsetY = clamp(Math.round(startOY + (e.clientY - startY) / previewScale), MODEL_OFFSET_Y_MIN, MODEL_OFFSET_Y_MAX);

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
                composition.scale = clamp(composition.scale + delta, MODEL_SCALE_MIN, MODEL_SCALE_MAX);
                scaleInput.value = composition.scale;
                scaleVal.textContent = composition.scale + '%';
                updatePreviewSourceScaleForZoom();
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

        previewEl.addEventListener('contextmenu', (e) => {
            if (activeTab !== 'decor-tab') return;
            e.preventDefault();
            cycleStickerSelectionAtPointer(e);
        });
    }

    // ====== 导出 ======
    async function doExport(type) {
        if (!currentCharaName) return;
        if (!isModelLoaded) {
            alert(t('cardExport.modelStillLoading', '模型仍在加载，请稍后再保存'));
            return;
        }

        try {
            let response;

            primaryActionBusy = true;
            updatePrimaryActionAvailability();
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

            primaryActionBusy = false;
            updatePrimaryActionAvailability();
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
            primaryActionBusy = false;
            updatePrimaryActionAvailability();
            exportFullBtn.textContent = t('cardExport.exportFull', '导出角色卡');
        }
    }

    // ====== 保存卡面（maker 模式专用） ======
    async function doSaveCardFace(options = {}) {
        if (!currentCharaName) return;
        if (!isModelLoaded) {
            const message = t('cardExport.modelStillLoading', '模型仍在加载，请稍后再保存');
            if (!options.silent) {
                alert(message);
            }
            throw new Error(message);
        }

        try {
            primaryActionBusy = true;
            updatePrimaryActionAvailability();
            exportFullBtn.textContent = options.statusText || t('cardExport.savingCardFace', '保存中...');

            const cardBlob = await renderFullCard(options.renderOptions || {});
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
                primaryActionBusy = false;
                updatePrimaryActionAvailability();
                cardFaceSaved = true;
                notifyCardFaceUpdated(currentCharaName);
                return { status: 'partial', error: respJson.error || '' };
            }

            cardFaceSaved = true;
            notifyCardFaceUpdated(currentCharaName);

            exportFullBtn.textContent = t('cardExport.saveCardFaceSuccess', '保存成功！');
            const saveResult = { status: 'ok' };
            if (options.closeAfterSave) {
                setTimeout(() => window.close(), 300);
                return saveResult;
            }
            setTimeout(() => {
                primaryActionBusy = false;
                updatePrimaryActionAvailability();
                exportFullBtn.textContent = t('cardExport.saveCardFace', '保存卡面');
            }, 1500);
            return saveResult;
        } catch (e) {
            console.error('[CardMaker] 保存卡面失败:', e);
            if (!options.silent) {
                alert(t('cardExport.saveCardFaceFailed', '保存失败: ' + e.message, { error: e.message }));
            }
            primaryActionBusy = false;
            updatePrimaryActionAvailability();
            exportFullBtn.textContent = t('cardExport.saveCardFace', '保存卡面');
            throw e;
        }
    }

    async function doAutoSaveDefaultCardFace() {
        try {
            resetComposition();
            clearAllStickers();
            await waitForCondition(() => isModelLoaded, 10000, '模型加载');
            await new Promise(resolve => setTimeout(resolve, 300));
            await doSaveCardFace({
                closeAfterSave: closeAfterAutoSave,
                silent: true,
                statusText: t('cardExport.autoSavingDefaultCardFace', '正在生成默认卡面...')
            });
        } catch (e) {
            console.error('[CardMaker] 自动生成默认卡面失败:', e);
            exportFullBtn.textContent = t('cardExport.autoSaveDefaultCardFaceFailed', '默认卡面生成失败');
        }
    }

    function notifyCardFaceUpdated(name) {
        const message = {
            type: 'card-face-updated',
            name,
            timestamp: Date.now()
        };
        if (fallbackDefaultOnClose && fallbackToken) {
            message.fallbackToken = fallbackToken;
        }

        if (window.opener) {
            try {
                window.opener.postMessage(message, window.location.origin);
            } catch (_) {}
            try {
                window.opener.opener?.postMessage(message, window.location.origin);
            } catch (_) {}

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

        try {
            const channel = new BroadcastChannel('neko-card-face-events');
            channel.postMessage(message);
            channel.close();
        } catch (_) {}

        try {
            localStorage.setItem('neko_card_face_event', JSON.stringify(message));
            localStorage.removeItem('neko_card_face_event');
        } catch (_) {}
    }

    /**
     * 根据构图参数渲染最终立绘 Blob
     * 输出尺寸与卡面预览比例一致，内部用 2 倍像素导出，确保所见即所得且更清晰。
     */
    async function renderFinalPortrait(options = {}) {
        const outputScale = Number.isFinite(options.outputScale) ? options.outputScale : CARD_OUTPUT_SCALE;
        const previousSourceScale = activeModelSourceScale;
        const exportSourceScale = Math.max(
            outputScale,
            getExportSourceScaleForZoom(options.composition || composition)
        );

        if (activeModelSourceScale !== exportSourceScale) {
            resizeModelRendererForCard(currentModelType, exportSourceScale);
        }
        ensureRender();

        const srcCanvas = getModelCanvas();
        const srcSize = getDrawableSourceSize(srcCanvas);
        if (!srcCanvas || srcSize.width <= 0 || srcSize.height <= 0) {
            if (activeModelSourceScale !== previousSourceScale) {
                resizeModelRendererForCard(currentModelType, previousSourceScale);
                ensureRender();
            }
            return null;
        }

        const cardW = CARD_BASE_WIDTH * outputScale;
        const cardH = CARD_BASE_HEIGHT * outputScale;
        const outW = cardW;
        const outH = cardH;

        const outCanvas = document.createElement('canvas');
        outCanvas.width = outW;
        outCanvas.height = outH;
        const ctx = outCanvas.getContext('2d');
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';

        ctx.fillStyle = '#E8F4F8';
        ctx.fillRect(0, 0, outW, outH);

        // 绘制顺序：模型下方贴纸 → 模型 → 模型上方贴纸。
        // layerOrder 是面板从上到下的顺序，canvas 需要从下到上绘制才会与图层面板一致。
        const includeStickers = options.includeStickers !== false;
        const stickerOrder = includeStickers
            ? layerOrder
                .filter(e => e.type === 'sticker')
                .map(e => stickers.find(s => s.id === e.id))
                .filter(Boolean)
            : [];
        const belowStickers = stickerOrder.filter(s => s.layer === 'below').reverse();
        const aboveStickers = stickerOrder.filter(s => s.layer === 'above').reverse();

        try {
            if (belowStickers.length > 0) {
                await drawStickerList(ctx, belowStickers, outW, outH);
            }

            drawModelWithComposition(ctx, srcCanvas, outW, outH, options.composition);

            if (aboveStickers.length > 0) {
                await drawStickerList(ctx, aboveStickers, outW, outH);
            }

            return await new Promise((resolve) => {
                outCanvas.toBlob((blob) => resolve(blob), 'image/png');
            });
        } finally {
            if (activeModelSourceScale !== previousSourceScale) {
                resizeModelRendererForCard(currentModelType, previousSourceScale);
                ensureRender();
                refreshPreview();
            }
        }
    }

    /**
     * 渲染完整角色卡用于卡面保存。
     * 输出尺寸 1200×1600，不再绘制角色名称栏。
     */
    async function renderFullCard(options = {}) {
        const cardBlob = await renderFinalPortrait(options);
        if (!cardBlob) {
            console.warn('[card_maker] renderFinalPortrait returned null, aborting card render');
            return null;
        }
        return cardBlob;
    }

    // ====== 贴纸系统 ======

    function initStickerGrid() {
        const grid = $('#sticker-grid');
        if (!grid) return;
        STICKER_LIBRARY_ITEMS.forEach(itemConfig => {
            grid.appendChild(createStickerLibraryItem(itemConfig));
        });
        // "导入自定义贴纸"按钮
        const importItem = document.createElement('div');
        importItem.className = 'sticker-item sticker-import-btn';
        importItem.innerHTML = '<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
        importItem.title = t('cardExport.importSticker', '导入自定义贴纸');
        importItem.addEventListener('click', () => importCustomSticker());
        grid.appendChild(importItem);

        // 从 localStorage 恢复已保存的自定义贴纸
        loadCustomStickers();
    }

    function iconStickerPath(file) {
        return `/static/icons/${file}`;
    }

    function getStickerLibraryVariants(itemConfig) {
        if (typeof itemConfig === 'string') return [iconStickerPath(itemConfig)];
        const files = Array.isArray(itemConfig?.variants) && itemConfig.variants.length
            ? itemConfig.variants
            : [itemConfig?.file].filter(Boolean);
        return files.map(iconStickerPath);
    }

    function createStickerLibraryItem(itemConfig) {
        const variants = getStickerLibraryVariants(itemConfig);
        let activeVariantIndex = 0;
        const item = document.createElement('div');
        item.className = 'sticker-item' + (variants.length > 1 ? ' sticker-variant-item' : '');

        const img = document.createElement('img');
        img.draggable = false;
        item.appendChild(img);

        const syncPreview = () => {
            const src = variants[activeVariantIndex] || variants[0];
            img.src = src;
            img.alt = src.split('/').pop().replace(/\.\w+$/, '');
            item.dataset.stickerSrc = src;
        };
        syncPreview();

        item.tabIndex = 0;
        item.setAttribute('role', 'button');
        const addActiveVariantSticker = () => addSticker(variants[activeVariantIndex]);
        item.addEventListener('click', addActiveVariantSticker);
        item.addEventListener('keydown', (event) => {
            if (event.target !== item) return;
            const isEnter = event.key === 'Enter' || event.keyCode === 13;
            const isSpace = event.key === ' ' || event.keyCode === 32;
            if (!isEnter && !isSpace) return;
            if (isSpace) event.preventDefault();
            addActiveVariantSticker();
        });

        if (variants.length > 1) {
            const switchBtn = document.createElement('button');
            switchBtn.type = 'button';
            switchBtn.className = 'sticker-variant-toggle-btn';
            switchBtn.innerHTML = STICKER_VARIANT_ICON;
            const label = t('cardExport.switchStickerVariant', '切换形态');
            switchBtn.title = label;
            switchBtn.setAttribute('aria-label', label);
            switchBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                activeVariantIndex = (activeVariantIndex + 1) % variants.length;
                syncPreview();
            });
            item.appendChild(switchBtn);
        }

        return item;
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
        const layerInsertIndex = getStickerInsertIndexForCurrentLayer();

        const el = document.createElement('img');
        el.src = src;
        el.className = 'sticker-placed';
        el.draggable = false;
        el.dataset.stickerId = id;
        sticker.imgEl = el;

        updateStickerElement(sticker);
        overlay.appendChild(el);
        stickers.push(sticker);
        layerOrder.splice(layerInsertIndex, 0, { type: 'sticker', id });

        // 选中新贴纸
        selectSticker(id);
        applyLayerOrderToStickers();
        refreshLayerPanel();

        // 贴纸拖拽
        setupStickerDrag(sticker, el);
    }

    function getStickerInsertIndexForCurrentLayer() {
        syncLayerOrder();
        if (modelLayerSelected) {
            const modelIdx = layerOrder.findIndex(e => e.type === 'model');
            return modelIdx >= 0 ? modelIdx : 0;
        }
        if (selectedStickerId != null) {
            const selectedIdx = layerOrder.findIndex(e => e.type === 'sticker' && e.id === selectedStickerId);
            if (selectedIdx >= 0) return selectedIdx;
        }
        return 0;
    }

    function normalizeStickerSrc(src) {
        if (!src || typeof src !== 'string') return '';
        try {
            const url = new URL(src, window.location.origin);
            if (url.origin === window.location.origin) return url.pathname;
        } catch (_) {}
        return src;
    }

    function getStickerVariantGroup(src) {
        const normalized = normalizeStickerSrc(src);
        return STICKER_VARIANT_GROUPS.find(group => group.includes(normalized)) || null;
    }

    function getNextStickerVariant(src) {
        const group = getStickerVariantGroup(src);
        if (!group || group.length < 2) return '';
        const normalized = normalizeStickerSrc(src);
        const currentIndex = group.indexOf(normalized);
        return group[(Math.max(currentIndex, 0) + 1) % group.length];
    }

    function updateStickerVariantControl(s) {
        const row = $('#sticker-variant-row');
        const btn = $('#sticker-switch-variant-btn');
        if (!row || !btn) return;
        const canSwitch = !!(s && getNextStickerVariant(s.src));
        row.style.display = canSwitch ? '' : 'none';
        btn.disabled = !canSwitch;
        const label = t('cardExport.switchStickerVariant', '切换形态');
        btn.title = label;
        btn.setAttribute('aria-label', label);
    }

    function switchSelectedStickerVariant() {
        const s = getSelectedSticker();
        const nextSrc = s ? getNextStickerVariant(s.src) : '';
        if (!s || !nextSrc) return;
        s.src = nextSrc;
        if (s.imgEl) s.imgEl.src = nextSrc;
        updateStickerVariantControl(s);
        refreshLayerPanel();
    }

    function updateStickerElement(s) {
        const el = s.imgEl;
        if (!el) return;
        el.style.width = s.w + 'px';
        el.style.height = s.h + 'px';
        el.style.left = `calc(${s.x}% - ${s.w / 2}px)`;
        el.style.top = `calc(${s.y}% - ${s.h / 2}px)`;
        el.style.transform = `rotate(${s.rotation}deg)`;
        if (s.id === selectedStickerId) {
            updateStickerSelectionFrame(s);
            updateRotateHandle(s);
        }
    }

    function updateStickerSelectionFrame(s) {
        const frame = $('#sticker-selection-frame');
        if (!frame) return;
        if (!s || activeTab !== 'decor-tab' || modelLayerSelected) {
            frame.classList.remove('visible');
            return;
        }
        frame.classList.add('visible');
        frame.style.width = s.w + 'px';
        frame.style.height = s.h + 'px';
        frame.style.left = `calc(${s.x}% - ${s.w / 2}px)`;
        frame.style.top = `calc(${s.y}% - ${s.h / 2}px)`;
        frame.style.transform = `rotate(${s.rotation}deg)`;
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
            el.style.pointerEvents = (enabled && !modelLayerSelected) ? 'auto' : 'none';
        });
        // 模型模式显示拖拽光标，装饰模式显示默认光标
        const preview = $('#card-preview');
        if (preview) {
            preview.style.cursor = (activeTab === 'model-tab') ? 'grab' : 'default';
        }
        // 非装饰模式隐藏旋转手柄
        if (!enabled) {
            updateRotateHandle(null);
            updateStickerSelectionFrame(null);
            modelLayerSelected = false;
            const area = $('#card-portrait-area');
            if (area) area.classList.remove('model-focused');
        } else if (!modelLayerSelected) {
            const s = getSelectedSticker();
            updateStickerSelectionFrame(s);
            updateRotateHandle(s);
        }
        updateStickerOverlayOrder();
    }

    function isPointerInsideStickerSelectionBox(s, clientX, clientY) {
        const area = $('#card-portrait-area');
        if (!area || !s) return false;
        const rect = area.getBoundingClientRect();
        const centerX = rect.left + (s.x / 100) * rect.width;
        const centerY = rect.top + (s.y / 100) * rect.height;
        const dx = clientX - centerX;
        const dy = clientY - centerY;
        const rad = s.rotation * Math.PI / 180;
        const localX = dx * Math.cos(rad) + dy * Math.sin(rad);
        const localY = -dx * Math.sin(rad) + dy * Math.cos(rad);
        return Math.abs(localX) <= s.w / 2 && Math.abs(localY) <= s.h / 2;
    }

    function getStickerDragTarget(hitSticker, event) {
        const selected = getSelectedSticker();
        if (
            selected &&
            selected.id !== hitSticker.id &&
            isPointerInsideStickerSelectionBox(selected, event.clientX, event.clientY)
        ) {
            return selected;
        }
        return hitSticker;
    }

    function getStickersAtPointer(clientX, clientY) {
        syncLayerOrder();
        const ordered = layerOrder
            .filter(entry => entry.type === 'sticker')
            .map(entry => stickers.find(s => s.id === entry.id))
            .filter(Boolean);
        stickers.forEach(s => {
            if (!ordered.includes(s)) ordered.push(s);
        });
        return ordered.filter(s => isPointerInsideStickerSelectionBox(s, clientX, clientY));
    }

    function cycleStickerSelectionAtPointer(event) {
        const candidates = getStickersAtPointer(event.clientX, event.clientY);
        if (candidates.length === 0) return;
        const currentIdx = candidates.findIndex(s => s.id === selectedStickerId);
        const nextIdx = currentIdx >= 0 ? (currentIdx + 1) % candidates.length : 0;
        selectSticker(candidates[nextIdx].id);
        refreshLayerPanel();
    }

    function setupStickerDrag(sticker, el) {
        let dragging = false;
        let dragTarget = null;
        let startX, startY, startPctX, startPctY;

        el.addEventListener('pointerdown', (e) => {
            if (activeTab !== 'decor-tab') return;
            if (modelLayerSelected) return;
            if (e.button !== 0) return;
            dragTarget = getStickerDragTarget(sticker, e);
            if (dragTarget.id !== selectedStickerId) {
                selectSticker(dragTarget.id);
                refreshLayerPanel();
            }
            e.stopPropagation();
            dragging = true;
            startX = e.clientX;
            startY = e.clientY;
            startPctX = dragTarget.x;
            startPctY = dragTarget.y;
            el.setPointerCapture(e.pointerId);
        });

        el.addEventListener('pointermove', (e) => {
            if (!dragging || !dragTarget) return;
            e.stopPropagation();
            const area = $('#card-portrait-area');
            const rect = area.getBoundingClientRect();
            const dx = (e.clientX - startX) / rect.width * 100;
            const dy = (e.clientY - startY) / rect.height * 100;
            dragTarget.x = clamp(startPctX + dx, 0, 100);
            dragTarget.y = clamp(startPctY + dy, 0, 100);
            updateStickerElement(dragTarget);
        });

        const stop = () => {
            dragging = false;
            dragTarget = null;
        };
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
            const isSelected = Number(el.dataset.stickerId) === id;
            el.classList.toggle('selected', isSelected);
            el.style.pointerEvents = (activeTab === 'decor-tab' && !modelLayerSelected) ? 'auto' : 'none';
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
            updateStickerVariantControl(s);
            updateStickerSelectionFrame(s);
            updateRotateHandle(s);
        } else if (controls) {
            controls.style.display = 'none';
            updateStickerVariantControl(null);
            updateStickerSelectionFrame(null);
            updateRotateHandle(null);
        }
        updateStickerOverlayOrder();
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
        // layerOrder 是面板从上到下的顺序，DOM 同层叠放要从下到上 append。
        const ordered = layerOrder
            .filter(e => e.type === 'sticker')
            .map(e => stickers.find(s => s.id === e.id))
            .filter(Boolean);
        // 补上不在 layerOrder 中的贴纸（安全兜底）
        stickers.forEach(s => { if (!ordered.includes(s)) ordered.push(s); });
        ordered.slice().reverse().forEach(s => {
            const target = (s.layer === 'below') ? below : above;
            target.appendChild(s.imgEl);
        });
    }

    function getSelectedSticker() {
        return stickers.find(s => s.id === selectedStickerId) || null;
    }

    function getStickerSelectionSuccessorId(deletedId) {
        syncLayerOrder();
        const deletedIdx = layerOrder.findIndex(e => e.type === 'sticker' && e.id === deletedId);
        if (deletedIdx >= 0) {
            for (let i = deletedIdx + 1; i < layerOrder.length; i++) {
                const entry = layerOrder[i];
                if (entry.type === 'sticker' && entry.id !== deletedId && stickers.find(s => s.id === entry.id)) {
                    return entry.id;
                }
            }
            for (let i = deletedIdx - 1; i >= 0; i--) {
                const entry = layerOrder[i];
                if (entry.type === 'sticker' && entry.id !== deletedId && stickers.find(s => s.id === entry.id)) {
                    return entry.id;
                }
            }
        }
        const fallback = stickers.find(s => s.id !== deletedId);
        return fallback ? fallback.id : null;
    }

    function removeStickerById(id) {
        const idx = stickers.findIndex(s => s.id === id);
        if (idx === -1) return;
        const deletingSelectedSticker = selectedStickerId === id;
        const nextStickerId = deletingSelectedSticker ? getStickerSelectionSuccessorId(id) : null;
        if (stickers[idx].imgEl) stickers[idx].imgEl.remove();
        stickers.splice(idx, 1);
        for (let i = layerOrder.length - 1; i >= 0; i--) {
            if (layerOrder[i].type === 'sticker' && layerOrder[i].id === id) {
                layerOrder.splice(i, 1);
            }
        }
        if (deletingSelectedSticker) {
            if (nextStickerId != null) {
                selectSticker(nextStickerId);
            } else {
                selectModelLayer({ refresh: false });
            }
        }
        updateStickerOverlayOrder();
        refreshLayerPanel();
    }

    function removeSelectedSticker() {
        if (selectedStickerId == null) return;
        removeStickerById(selectedStickerId);
    }

    function clearAllStickers() {
        stickers.forEach(s => {
            if (s.imgEl) s.imgEl.remove();
        });
        stickers.length = 0;
        for (let i = layerOrder.length - 1; i >= 0; i--) {
            if (layerOrder[i].type === 'sticker') layerOrder.splice(i, 1);
        }
        selectModelLayer({ refresh: false });
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

    function updatePrimaryActionAvailability() {
        if (!exportFullBtn) return;
        exportFullBtn.disabled = primaryActionBusy || isModelLoading || !isModelLoaded;
    }

    function syncCompositionControlLimits() {
        if (offsetXInput) {
            offsetXInput.min = String(MODEL_OFFSET_X_MIN);
            offsetXInput.max = String(MODEL_OFFSET_X_MAX);
        }
        if (offsetYInput) {
            offsetYInput.min = String(MODEL_OFFSET_Y_MIN);
            offsetYInput.max = String(MODEL_OFFSET_Y_MAX);
        }
        if (scaleInput) {
            scaleInput.min = String(MODEL_SCALE_MIN);
            scaleInput.max = String(MODEL_SCALE_MAX);
        }
    }

    function canCloseWhileLoading() {
        if (!isModelLoading) return true;
        if (allowCloseWhileLoading) return true;
        return modelLoadingStartedAt > 0 &&
            Date.now() - modelLoadingStartedAt >= MODEL_LOADING_CLOSE_FALLBACK_MS;
    }

    function scheduleLoadingCloseFallback() {
        if (loadingCloseFallbackTimer) {
            window.clearTimeout(loadingCloseFallbackTimer);
        }
        loadingCloseFallbackTimer = window.setTimeout(() => {
            loadingCloseFallbackTimer = null;
            if (!isModelLoading) return;
            allowCloseWhileLoading = true;
            updateCardMakerInteractivity(true);
        }, MODEL_LOADING_CLOSE_FALLBACK_MS);
    }

    function clearLoadingCloseFallback() {
        if (loadingCloseFallbackTimer) {
            window.clearTimeout(loadingCloseFallbackTimer);
            loadingCloseFallbackTimer = null;
        }
        modelLoadingStartedAt = 0;
        allowCloseWhileLoading = false;
    }

    function updateCardMakerInteractivity(locked) {
        const isLocked = !!locked;
        const allowLoadingClose = isLocked && canCloseWhileLoading();
        document.body?.classList.toggle('card-maker-loading', isLocked);

        const controls = document.querySelectorAll(
            '#control-panel button, #control-panel input, #control-panel select, #control-panel textarea, ' +
            '.page-title-bar button, [data-neko-window-control]'
        );
        controls.forEach(control => {
            if (control === exportFullBtn) return;
            const isCloseControl = control === backBtn ||
                control.getAttribute('data-neko-window-control') === 'close';
            const shouldDisable = isLocked && !(allowLoadingClose && isCloseControl);
            control.disabled = shouldDisable;
            control.setAttribute('aria-disabled', shouldDisable ? 'true' : 'false');
        });
        updatePrimaryActionAvailability();
    }

    function showLoading(show) {
        const nextLoading = !!show;
        if (nextLoading && !isModelLoading) {
            modelLoadingStartedAt = Date.now();
            allowCloseWhileLoading = false;
            scheduleLoadingCloseFallback();
        } else if (!nextLoading) {
            clearLoadingCloseFallback();
        }
        isModelLoading = nextLoading;
        if (show) {
            loadingOverlay.classList.remove('hidden');
        } else {
            loadingOverlay.classList.add('hidden');
        }
        updateCardMakerInteractivity(show);
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
        // 添加不在 layerOrder 中的新贴纸（安全兜底：默认插到最上层）
        stickers.forEach(s => {
            if (!layerOrder.find(e => e.type === 'sticker' && e.id === s.id)) {
                layerOrder.splice(0, 0, { type: 'sticker', id: s.id });
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

    function selectModelLayer(options = {}) {
        const refresh = options.refresh !== false;
        modelLayerSelected = true;
        selectedStickerId = null;
        document.querySelectorAll('.sticker-placed').forEach(el => {
            el.classList.remove('selected');
            el.style.pointerEvents = 'none';
        });
        updateRotateHandle(null);
        updateStickerSelectionFrame(null);
        updateStickerVariantControl(null);
        const controls = $('#sticker-controls');
        if (controls) controls.style.display = 'none';
        const area = $('#card-portrait-area');
        if (area) area.classList.add('model-focused');
        updateStickerOverlayOrder();
        if (refresh) refreshLayerPanel();
    }

    function createModelLayerItem(orderIdx) {
        const item = document.createElement('div');
        item.className = 'layer-item is-model' + (modelLayerSelected ? ' selected' : '');
        item.dataset.layerIdx = orderIdx;
        item.draggable = true;
        item.innerHTML = `<span class="layer-item-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="10" r="3"/><path d="M6 21v-1a6 6 0 0112 0v1"/></svg></span><span class="layer-item-name">${t('cardExport.modelLayer', '模型')}</span><span class="layer-drag-handle">⠿</span>`;

        item.addEventListener('click', () => {
            selectModelLayer();
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
