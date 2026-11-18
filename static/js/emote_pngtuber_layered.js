/*
 * Native layered-package builder for the Emote maker.
 *
 * Emits the manifest + layer sheets that `static/pngtuber-core.js` consumes in layered
 * mode, so an avatar exported from the emote maker can carry more than the eight flat
 * slots and can be driven by name at runtime.
 *
 * Three behaviours here are load-bearing and were verified against the runtime, not guessed:
 *
 * 1. `layerStateForCurrentIndex` resolves with `states[this.layeredStateIndex]` — a
 *    POSITIONAL array index (pngtuber-core.js:1060-1063). `state_index` inside an entry is
 *    metadata; the runtime never searches by it. So every layer's `states` array must be
 *    `state_count` long, and slots where the layer must not paint need an explicit
 *    `visible: false`. Getting this wrong yields a package that loads and draws nothing.
 *
 * 2. Animated sheets advance as `floor(elapsed * animation_speed * 4)` — the ×4 is
 *    REMIX_FRAME_SPEED_MULTIPLIER (pngtuber-core.js:16, :1311). So to play at a given fps,
 *    `animation_speed` must be fps/4, not fps.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmotePngtuberLayered = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const RUNTIME = 'layered_canvas';
    const SOURCE_FORMAT = 'neko_emote_maker';
    const ADAPTER = 'layered_canvas_v1';
    const ADAPTER_VERSION = 2;
    // pngtuber-core.js:16 — 帧进度 = floor(秒 * animation_speed * 4)
    const FRAME_SPEED_MULTIPLIER = 4;
    const MAX_STATES = 64;
    const SHEET_MAX_EDGE = 4096; // Rows wrap at this width.
    const SHEET_MAX_HEIGHT = 8192;
    const LAYER_DIR = 'layers';

    const PngtuberLayeredError = class extends Error {
        constructor(message, code = 'layered_package_error') {
            super(message);
            this.name = 'PngtuberLayeredError';
            this.code = code;
        }
    };

    function positiveInt(value, fallback) {
        const number = Number(value);
        if (!Number.isFinite(number) || number <= 0) return fallback;
        return Math.round(number);
    }

    // 情绪名 -> 状态序号。运行时平铺模式靠 EMOTION_IMAGE_KEYS，分层模式靠
    // emotion_mappings（pngtuber-core.js:642-650），两边名字要对得上。
    const EMOTION_NAMES = Object.freeze(['happy', 'sad', 'angry', 'surprised']);

    function buildLayeredPackage(input) {
        const states = Array.isArray(input && input.states) ? input.states : [];
        if (!states.length) throw new PngtuberLayeredError('没有任何状态', 'no_states');
        if (states.length > MAX_STATES) {
            throw new PngtuberLayeredError(`状态数超过上限 ${MAX_STATES}`, 'too_many_states');
        }
        const seen = new Set();
        states.forEach((state) => {
            const key = state && state.stateKey;
            if (!key || seen.has(key)) {
                throw new PngtuberLayeredError('状态键重复或缺失', 'bad_state_key', { key });
            }
            seen.add(key);
        });

        const frameWidth = positiveInt(states[0].width, 512);
        const frameHeight = positiveInt(states[0].height, 512);
        const canvasWidth = positiveInt(input.canvasWidth, frameWidth);
        const canvasHeight = positiveInt(input.canvasHeight, frameHeight);

        const stateCatalog = [];
        const layers = [];
        const files = [];

        states.forEach((state, index) => {
            const frames = positiveInt(state.frames, 1);
            const frameW = positiveInt(state.width, frameWidth);
            const frameH = positiveInt(state.height, frameHeight);
            const sheetWidth = positiveInt(state.sheetWidth, frameW * frames);
            const sheetHeight = positiveInt(state.sheetHeight, frameH);
            if (sheetWidth > SHEET_MAX_EDGE || sheetHeight > SHEET_MAX_HEIGHT) {
                // 与其悄悄截断成错位的图，不如报错：调用方可以改成纵向分块或多层。
                throw new PngtuberLayeredError(
                    `状态 ${state.stateKey} 的雪碧图 ${sheetWidth}x${sheetHeight} 超过 ${SHEET_MAX_EDGE}x${SHEET_MAX_HEIGHT}px 上限`,
                    'sheet_too_large',
                    { stateKey: state.stateKey, sheetWidth, sheetHeight },
                );
            }
            if (!state.pngBytes || !state.pngBytes.length) {
                throw new PngtuberLayeredError(`状态 ${state.stateKey} 缺少贴图数据`, 'missing_sheet');
            }
            const sampledFps = Number(state.fps);
            const fps = Number.isFinite(sampledFps) && sampledFps > 0 ? sampledFps : 12;
            const path = `${LAYER_DIR}/${index}-${state.stateKey}.png`;
            // 网格从"这张表实际有多大"推出来，而不是采信调用方报的数字：超过
            // 4096px 的表会被合成方折成多行，运行时按 sx=(frame%hframes)*fw、
            // sy=floor(frame/hframes)*fh 取格（见 pngtuber-core.js:1324），谎报成
            // "一行摆完"就会把每格切成窄条、取到图外，形象于是来回闪空白。
            // 贴图尺寸是随包发出去的事实，metadata 只能服从它，不能和它各说一套。
            const gridH = Math.max(1, Math.round(sheetWidth / frameW));
            const gridV = Math.max(1, Math.round(sheetHeight / frameH));
            const cells = gridH * gridV;
            // 真实帧数，不是补齐后的格子数：把空格当动画帧会播出一段空白。
            // 也绝不能超过格子数，否则最后几帧永远取到图外。
            const realFrames = Math.min(cells, positiveInt(state.frameCount, cells));

            const own = {
                state_index: index,
                frame: 0,
                hframes: gridH,
                vframes: gridV,
                frames: realFrames,
                frame_width: frameW,
                frame_height: frameH,
                image_width: sheetWidth,
                image_height: sheetHeight,
                // fps 换算见文件头注释第 2 点
                animation_speed: fps / FRAME_SPEED_MULTIPLIER,
                x: Number.isFinite(Number(state.x)) ? Number(state.x) : 0,
                y: Number.isFinite(Number(state.y)) ? Number(state.y) : 0,
                // 分层绘制在"自己那个位置"读 layerState.scale / rotation
                // （pngtuber-core.js:2338、:2502），所以每个状态可以有独立的大小和角度
                scale: Number.isFinite(Number(state.scale)) ? Number(state.scale) : 1,
                rotation: Number.isFinite(Number(state.rotation)) ? Number(state.rotation) : 0,
                visible: true,
                // 口型/眨眼交给包外处理：我们烘的是合成后的整幅画面，
                // 谎报这两个会让运行时去做没有意义的层门控。
                should_talk: false,
                should_blink: false,
            };
            // 位置数组：非本店的位置显式不可见（见文件头注释第 1 点）
            const layerStates = states.map((_, position) => (
                position === index ? { ...own } : {
                    state_index: position,
                    frame: 0,
                    hframes: 1,
                    vframes: 1,
                    frames: 1,
                    animation_speed: 0,
                    visible: false,
                    // 占位槽也写死中性变换：一旦可见性判定改动，
                    // 未继承层数值的位置不会突然带上别的状态的大小和角度
                    scale: 1,
                    rotation: 0,
                    x: 0,
                    y: 0,
                    should_talk: false,
                    should_blink: false,
                }
            ));

            layers.push({
                id: `emote-${index}`,
                name: state.stateKey,
                image: path,
                order: index,
                zindex: index,
                x: own.x,
                y: own.y,
                scale: [own.scale, own.scale],
                rotation: own.rotation,
                visible: true,
                states: layerStates,
            });

            const entry = {
                state_index: index,
                state_number: index + 1,
                name: state.stateKey,
                label: state.label || state.stateKey,
                // 运行时传的是状态名（talking / drag / click / happy…），目录里写的是
                // 配置键（talking_image…）。把短名一并写成别名，外部 API 和用户按名字
                // 驱动才都能命中同一格。
                aliases: Array.isArray(state.aliases) && state.aliases.length
                    ? state.aliases.slice(0, 8)
                    : [String(state.stateKey || '').replace(/_image$/, '')].filter(Boolean),
            };
            if (state.hotkey) entry.hotkey = state.hotkey;
            stateCatalog.push(entry);

            files.push({ path, data: state.pngBytes });
        });

        const emotionMappings = {};
        stateCatalog.forEach((entry) => {
            EMOTION_NAMES.forEach((emotion) => {
                if (entry.name === `${emotion}_image` && !(emotion in emotionMappings)) {
                    emotionMappings[emotion] = { state_index: entry.state_index };
                }
            });
        });

        const metadata = {
            runtime: RUNTIME,
            source_format: SOURCE_FORMAT,
            adapter_version: ADAPTER_VERSION,
            canvas: { width: canvasWidth, height: canvasHeight },
            state_count: stateCatalog.length,
            state_catalog: stateCatalog,
            emotion_mappings: emotionMappings,
            // 逐项显式写，缺省即关（pngtuber-core.js:1092-1096），省略等于 false
            runtime_features: {
                layer_motion: false,
                sprite_sheet_animation: true,
                layered_breathing: false,
                physics_v2: false,
                mesh_deformation: false,
                costume_change_bounce: false,
            },
            capabilities: {
                authored_by: 'neko-emote-maker',
                animated_states: true,
                // 我们烘的是合成图，没有真实的网格/物理数据，这里必须如实报
                mesh_metadata: false,
                mesh_runtime: false,
                physics_runtime: false,
                hotkeys: false,
            },
            unsupported_features: ['per-layer mouth/eye gating', 'physics', 'mesh deformation'],
            blink: { enabled: false },
            layers,
        };

        return {
            metadata,
            adapter: ADAPTER,
            metadataPath: 'metadata.json',
            files: files.concat([{
                path: 'metadata.json',
                data: new TextEncoder().encode(JSON.stringify(metadata, null, 2)),
            }]),
        };
    }

    return Object.freeze({
        PngtuberLayeredError,
        ADAPTER,
        RUNTIME,
        SOURCE_FORMAT,
        FRAME_SPEED_MULTIPLIER,
        MAX_STATES,
        SHEET_MAX_EDGE,
        EMOTION_NAMES,
        buildLayeredPackage,
    });
}));
