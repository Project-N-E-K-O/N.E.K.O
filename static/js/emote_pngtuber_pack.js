/*
 * PNGTuber avatar-pack builder for the Emote maker.
 *
 * Turns already-encoded per-state GIF bytes into a native N.E.K.O. PNGTuber
 * package. Kept deliberately pure (bytes in, files out) so it is testable in Node
 * without a DOM, and so neither the Spine renderer nor the PNGTuber runtime is
 * reachable from here.
 *
 * Two exits share the same output:
 *   - packToZip()      -> handed to the user via <a download>; they unzip it and
 *                         folder-import it in 模型管理.
 *   - appendMultipart() -> posted straight to /api/model/pngtuber/upload_model,
 *                         which takes one multipart part per file with the
 *                         relative path as the filename (that is exactly what
 *                         static/js/model_manager/page-controller.js sends for a
 *                         picked directory, so both routes hit the same
 *                         validation, limits, no-overwrite and atomic-publish
 *                         code). There is no ZIP path on that endpoint.
 *
 * Only `idle_image` is required by the runtime; 模型 missing states fall back to
 * idle inside normalizeConfig(), so omitting a state is safe and intentional.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory(
            typeof require === 'function' ? require('../libs/fflate.umd.js') : null,
        );
        return;
    }

    root.NekoEmotePngtuberPack = factory(root.fflate);
}(typeof window !== 'undefined' ? window : globalThis, function (fflate) {
    'use strict';

    const MODEL_TYPE = 'pngtuber';
    const MAX_FOLDER_CHARS = 64;

    // Mirrors IMAGE_KEYS in static/pngtuber-core.js:5. Order is UI order; the
    // first four are the state images, the rest are emotion overlays that
    // setEmotion() swaps in.
    const STATES = Object.freeze([
        { key: 'idle_image', file: 'idle.gif', labelKey: 'emoteMaker.packStateIdle', required: true },
        { key: 'talking_image', file: 'talking.gif', labelKey: 'emoteMaker.packStateTalking' },
        { key: 'drag_image', file: 'drag.gif', labelKey: 'emoteMaker.packStateDrag' },
        { key: 'click_image', file: 'click.gif', labelKey: 'emoteMaker.packStateClick' },
        { key: 'happy_image', file: 'happy.gif', labelKey: 'emoteMaker.packStateHappy' },
        { key: 'sad_image', file: 'sad.gif', labelKey: 'emoteMaker.packStateSad' },
        { key: 'angry_image', file: 'angry.gif', labelKey: 'emoteMaker.packStateAngry' },
        { key: 'surprised_image', file: 'surprised.gif', labelKey: 'emoteMaker.packStateSurprised' },
    ]);

    const STATE_KEYS = Object.freeze(STATES.map((state) => state.key));

    // 落位参数直接写进 model.json 的 pngtuber 块，运行时用 CSS transform 施加：
    // scale 被 clampNumber 夹在 0.1..5，offset 是 PNGTuber 自己的像素单位（与
    // 表情画布单位无关，所以不能照搬调整面板里的 -100..100），position_anchor
    // 只有 center / bottom_right 两个值，写别的会被 normalizeConfig 归掉。
    const PLACEMENT_LIMITS = Object.freeze({
        scaleMin: 0.1,
        scaleMax: 5,
        offsetMax: 4000,
        anchors: Object.freeze(['center', 'bottom_right']),
    });
    const DEFAULT_PLACEMENT = Object.freeze({
        scale: 1,
        offsetX: 0,
        offsetY: 0,
        anchor: 'center',
        mirror: false,
    });

    function clampNumber(value, min, max, fallback) {
        const number = Number(value);
        if (!Number.isFinite(number)) return fallback;
        return Math.min(max, Math.max(min, number));
    }

    /**
     * 'talking'（默认）＝今天平铺包的行为：说话时不插情绪，话说完情绪自己补上。
     * 'emotion' ＝情绪生效期间说话不得覆盖它。
     * 只认这两个值，其余退回默认 —— 别让界面能选出一个运行时尚不认的值。
     */
    function normalizeStatePriority(value) {
        return String(value || '').trim().toLowerCase() === 'emotion' ? 'emotion' : 'talking';
    }

    function normalizePlacement(placement) {
        const source = placement && typeof placement === 'object' ? placement : {};
        const anchors = PLACEMENT_LIMITS.anchors;
        const requested = String(source.anchor || '');
        return {
            scale: Number(clampNumber(
                source.scale,
                PLACEMENT_LIMITS.scaleMin,
                PLACEMENT_LIMITS.scaleMax,
                DEFAULT_PLACEMENT.scale,
            ).toFixed(3)),
            offset_x: Math.round(clampNumber(
                source.offsetX, -PLACEMENT_LIMITS.offsetMax, PLACEMENT_LIMITS.offsetMax,
                DEFAULT_PLACEMENT.offsetX,
            )),
            offset_y: Math.round(clampNumber(
                source.offsetY, -PLACEMENT_LIMITS.offsetMax, PLACEMENT_LIMITS.offsetMax,
                DEFAULT_PLACEMENT.offsetY,
            )),
            anchor: anchors.includes(requested) ? requested : DEFAULT_PLACEMENT.anchor,
            mirror: source.mirror === true,
        };
    }

    class PngtuberPackError extends Error {
        constructor(message, code = 'pngtuber_pack_error', details = null) {
            super(message);
            this.name = 'PngtuberPackError';
            this.code = code;
            this.details = details;
        }
    }

    // 文件夹名只用于 zip 内的根目录；直连导入时服务端会用 model.json 的 name 另起
    // 目录（实测：name「自检包」→ folder「自检包」，zip 根目录名被忽略）。所以这里
    // 故意保持与 name 同一个值，两条路径才会落成同一个形象。非 ASCII 目录在本应用里
    // 本来就能用（用户库里已存在「测试猫猫」），真正要防的只有路径分隔与 ".."。
    /**
     * 列表卡片的"要不要重建"签名。刻意不含选中态：选中某一项不该重建整张列表
     * （真实包有 407 个场景），但任何会影响卡片外观的东西都必须让它重建 ——
     * 少一项、多一项、改名、只剩元数据，都要重建。
     */
    function listSignature(kind, items) {
        const list = Array.isArray(items) ? items : [];
        return [
            'kind=' + String(kind || ''),
            'n=' + list.length,
            // 每一项只取"会影响这张卡长什么样"的字段；选中态刻意不在这里
            list.map((item) => [
                item?.id ?? '',
                item?.name ?? item?.nameKey ?? '',
                item?.metadataOnly ? 'meta' : '',
            ].join(':')).join(','),
        ].join('/');
    }

    function packageFolderName(value, fallback = 'neko-emote-avatar') {
        const cleaned = String(value == null ? '' : value)
            .trim()
            .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '-')
            .replace(/\.{2,}/g, '.')
            .replace(/^[.\-_]+|[.\-_]+$/g, '')
            .slice(0, MAX_FOLDER_CHARS)
            .replace(/[.\-_]+$/, '');
        return cleaned || fallback;
    }

    // 拖拽落点的解析规则：场景卡片直接命中；动画卡片必须能找到引用它的那个场景，
    // 因为动画本身不带相机/描边/文字，硬用当前视角烘一张图会得到与预览不一致的结果。
    // 放在这里是为了可单测——它只依赖数据形状，不依赖 DOM。
    function resolveSceneForDrag(scenes, payload) {
        const list = Array.isArray(scenes) ? scenes : [];
        if (!payload || typeof payload !== 'object') return null;
        if (payload.kind === 'scene') {
            return list.find((scene) => scene && scene.id === payload.sceneId) || null;
        }
        if (payload.kind === 'animation') {
            const id = String(payload.animationId || '');
            if (!id) return null;
            return list.find((scene) => String(scene?.animation?.id || '') === id) || null;
        }
        return null;
    }

    // PNGTuber 平铺模式唯一的"时序"通道就是 GIF 自己的帧：包里没有地方存放
    // 触发条件、时长或过渡（normalizeConfig 只读 8 个 *_image + 布局字段），
    // 所以"这个状态演多久"只能靠烘多少帧来表达。
    // seconds === null/'' -> 演完整段动画；0 -> 单帧定格；>0 -> 按秒数换算帧数。
    function toBytes(data, label) {
        if (data instanceof Uint8Array) return data;
        if (data instanceof ArrayBuffer) return new Uint8Array(data);
        if (ArrayBuffer.isView(data)) {
            return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
        }
        throw new PngtuberPackError(`${label} 缺少可用的图像数据`, 'missing_asset', { label });
    }

    // states: [{ key, bytes }]; anything not in STATES is rejected rather than
    // silently written, so a typo cannot produce a package the runtime ignores.
    function buildPackageFiles({ folderName, displayName, description, states, placement,
                                layered, statePriority } = {}) {
        const folder = packageFolderName(folderName || displayName);
        const provided = Array.isArray(states) ? states : [];
        const byKey = new Map();
        const fileOverrides = new Map();
        for (const entry of provided) {
            if (!entry || !STATE_KEYS.includes(entry.key)) {
                throw new PngtuberPackError('包含未知的状态键', 'unknown_state', { key: entry && entry.key });
            }
            byKey.set(entry.key, toBytes(entry.bytes, entry.key));
            if (typeof entry.file === 'string' && entry.file) {
                if (entry.file.includes('/') || entry.file.includes('..')) {
                    throw new PngtuberPackError('图像文件名不能含路径', 'bad_file_name', { file: entry.file });
                }
                fileOverrides.set(entry.key, entry.file);
            }
        }
        if (!byKey.has('idle_image')) {
            throw new PngtuberPackError('缺少待机图像，PNGTuber 无法加载该包', 'missing_idle');
        }

        const normalizedPlacement = normalizePlacement(placement);
    // manifest 里写的名字和实际放进包里的名字必须同源：分开的两处一旦分歧，
    // 就是"idle_image 指向一个包里不存在的文件"，服务端校验或运行期 404。
    function stateFileName(state) {
        const override = fileOverrides.get(state.key);
        return override || state.file;
    }

        const pngtuber = {
            scale: normalizedPlacement.scale,
            offset_x: normalizedPlacement.offset_x,
            offset_y: normalizedPlacement.offset_y,
            position_anchor: normalizedPlacement.anchor,
            mirror: normalizedPlacement.mirror,
            // 运行时靠它决定"说话"和"情绪"谁压住谁；不写就是默认（说话优先）
            state_priority: normalizeStatePriority(statePriority),
            // 移动端字段与桌面同值：normalizeConfig 给 mobile_scale 的默认是
            // min(scale,1)、mobile_offset_* 默认 0，不写就等于用户设的落位在
            // 手机上被悄悄改掉。
            mobile_scale: normalizedPlacement.scale,
            mobile_offset_x: normalizedPlacement.offset_x,
            mobile_offset_y: normalizedPlacement.offset_y,
        };
        for (const state of STATES) {
            // 路径写相对形式：上传时服务端会给每个 *_image 补上
            // /user_pngtuber/<folder>/ 前缀，自己拼反而会被当成绝对路径原样放行。
            if (byKey.has(state.key)) pngtuber[state.key] = stateFileName(state);
        }

        const manifest = {
            name: String(displayName || folder).trim() || folder,
            description: String(description || '').trim(),
            model_type: MODEL_TYPE,
            pngtuber,
        };

        const files = [];
        for (const state of STATES) {
            if (!byKey.has(state.key)) continue;
            files.push({ path: `${folder}/${stateFileName(state)}`, data: byKey.get(state.key) });
        }
        if (layered && typeof layered === 'object') {
            if (!layered.adapter || !layered.metadataPath) {
                throw new PngtuberPackError('分层包缺少 adapter 或 metadata 路径', 'bad_layered');
            }
            // 服务端 _validate_model_package 要求 idle_image 非空且文件真实存在，
            // 所以分层包也必须带一张平铺回落图，不能只发 metadata。
            if (!byKey.has('idle_image')) {
                throw new PngtuberPackError('分层包缺少平铺回落图', 'missing_fallback');
            }
            pngtuber.adapter = layered.adapter;
            pngtuber.layered_metadata = layered.metadataPath;
            for (const sheet of Array.isArray(layered.files) ? layered.files : []) {
                if (!sheet || typeof sheet.path !== 'string' || sheet.path.includes('..')
                    || sheet.path.startsWith('/')) {
                    throw new PngtuberPackError('分层资源路径不合法', 'bad_layered_path', { path: sheet && sheet.path });
                }
                files.push({ path: `${folder}/${sheet.path}`, data: toBytes(sheet.data, sheet.path) });
            }
        }

        files.push({
            path: `${folder}/model.json`,
            data: new TextEncoder().encode(JSON.stringify(manifest, null, 2)),
        });
        return { folder, files, manifest };
    }

    function packToZip(files) {
        if (!fflate || typeof fflate.zipSync !== 'function') {
            throw new PngtuberPackError('ZIP 编码器不可用', 'zip_unavailable');
        }
        const entries = {};
        for (const file of files) {
            entries[file.path] = toBytes(file.data, file.path);
        }
        // zipSync 是同步且全内存的（与 emote_template_directory.js 的用法一致）；
        // worker 版异步 zip 并非所有 Electron/Node 上下文都可用。
        return fflate.zipSync(entries, { level: 6 });
    }

    function appendMultipart(files, form) {
        const target = form || (typeof FormData === 'function' ? new FormData() : null);
        if (!target || typeof target.append !== 'function') {
            throw new PngtuberPackError('FormData 不可用', 'formdata_unavailable');
        }
        for (const file of files) {
            const bytes = toBytes(file.data, file.path);
            // 第三参必须是带目录的相对路径，服务端靠它还原包内结构并做穿越检查。
            target.append('files', new Blob([bytes], { type: 'application/octet-stream' }), file.path);
        }
        return target;
    }

    function uploadUrl() {
        return '/api/model/pngtuber/upload_model';
    }

    return Object.freeze({
        PngtuberPackError,
        STATES,
        STATE_KEYS,
        MODEL_TYPE,
        PLACEMENT_LIMITS,
        DEFAULT_PLACEMENT,
        normalizePlacement,
        resolveSceneForDrag,
        packageFolderName,
        normalizeStatePriority,
        listSignature,
        buildPackageFiles,
        packToZip,
        appendMultipart,
        uploadUrl,
    });
}));
