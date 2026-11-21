/* Generic Spine ZIP renderer using the official 4.3 runtime. */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory(typeof globalThis !== 'undefined' ? globalThis : root);
        return;
    }

    root.NekoEmoteSpineRenderer = factory(root);
}(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    const REQUIRED_RUNTIME_SYMBOLS = Object.freeze([
        'ManagedWebGLRenderingContext',
        'SceneRenderer',
        'AssetManager',
        'TextureAtlas',
        'AtlasAttachmentLoader',
        'SkeletonBinary',
        'SkeletonJson',
        'Skeleton',
        'AnimationStateData',
        'AnimationState',
    ]);
    const DEFAULT_RUNTIME_VERSION = '4.3.13';
    const DEFAULT_BACKGROUND = Object.freeze([0, 0, 0, 0]);
    const QUAD_TRIANGLES = [0, 1, 2, 2, 3, 0];
    const LUMINANCE = [0.2126, 0.7152, 0.0722];

    function triangleWeights(vertices, indices, x, y) {
        const [a, b, c] = indices.map((index) => index * 2);
        const dx1 = vertices[b] - vertices[a];
        const dy1 = vertices[b + 1] - vertices[a + 1];
        const dx2 = vertices[c] - vertices[a];
        const dy2 = vertices[c + 1] - vertices[a + 1];
        const det = dx1 * dy2 - dy1 * dx2;
        if (Math.abs(det) < 1e-7) return null;
        const dx = x - vertices[a];
        const dy = y - vertices[a + 1];
        const u = (dx * dy2 - dy * dx2) / det;
        const v = (dy * dx1 - dx * dy1) / det;
        const w = 1 - u - v;
        return Math.min(u, v, w) >= -1e-6 ? [w, u, v] : null;
    }

    function meshBoneAt(attachment, indices, weights, bones) {
        if (!attachment.bones) return null;
        const contributions = new Map();
        const factors = new Map(indices.map((index, i) => [index, weights[i]]));
        const lastVertex = Math.max(...indices);
        let boneIndex = 0;
        let vertexIndex = 0;
        for (let vertex = 0; vertex <= lastVertex; vertex += 1) {
            const count = attachment.bones[boneIndex++];
            const factor = factors.get(vertex);
            if (factor > 0) {
                for (let i = 0; i < count; i += 1) {
                    const index = attachment.bones[boneIndex + i];
                    const weight = attachment.vertices[vertexIndex + i * 3 + 2] * factor;
                    contributions.set(index, (contributions.get(index) || 0) + weight);
                }
            }
            boneIndex += count;
            vertexIndex += count * 3;
        }
        let best = null;
        let bestWeight = 0;
        for (const [index, weight] of contributions) {
            if (weight > bestWeight && bones[index]?.active) {
                bestWeight = weight;
                best = bones[index];
            }
        }
        return best;
    }

    class SpineRendererError extends Error {
        constructor(message, code = 'renderer_error', details = undefined) {
            super(message);
            this.name = 'SpineRendererError';
            this.code = code;
            if (details !== undefined) this.details = details;
        }
    }

    function asPositiveNumber(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? number : fallback;
    }

    function normalizePath(value) {
        let path = String(value || '').replace(/\\/g, '/').normalize('NFC');
        if (!path || path.startsWith('/') || path.includes('\0')) return '';
        const parts = [];
        for (const part of path.split('/')) {
            if (!part || part === '.') continue;
            if (part === '..') return '';
            parts.push(part);
        }
        return parts.join('/');
    }

    function dirname(path) {
        const normalized = normalizePath(path);
        const index = normalized.lastIndexOf('/');
        return index >= 0 ? normalized.slice(0, index + 1) : '';
    }

    function basename(path) {
        const normalized = normalizePath(path);
        const index = normalized.lastIndexOf('/');
        return index >= 0 ? normalized.slice(index + 1) : normalized;
    }

    function joinPath(prefix, name) {
        const candidate = `${prefix || ''}${name || ''}`;
        return normalizePath(candidate);
    }

    function toBytes(value) {
        if (value instanceof Uint8Array) return value;
        if (value instanceof ArrayBuffer) return new Uint8Array(value);
        if (ArrayBuffer.isView(value)) {
            return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
        }
        throw new SpineRendererError('模板资源不是二进制数据', 'invalid_asset');
    }

    function utf8Bytes(value) {
        if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(String(value));
        // Node versions used by the smoke tests expose Buffer, while older
        // browser embeddings may only expose the global TextEncoder.
        if (typeof Buffer !== 'undefined') return new Uint8Array(Buffer.from(String(value), 'utf8'));
        throw new SpineRendererError('当前环境缺少 UTF-8 编码器', 'encoding_unavailable');
    }

    function base64Encode(bytes) {
        const input = toBytes(bytes);
        if (typeof Buffer !== 'undefined') return Buffer.from(input).toString('base64');
        if (typeof btoa !== 'function') {
            throw new SpineRendererError('当前环境缺少 Base64 编码器', 'encoding_unavailable');
        }
        let binary = '';
        const chunkSize = 0x8000;
        for (let index = 0; index < input.length; index += chunkSize) {
            binary += String.fromCharCode(...input.subarray(index, index + chunkSize));
        }
        return btoa(binary);
    }

    function bytesToDataUri(bytes, mimeType) {
        return `data:${mimeType};base64,${base64Encode(bytes)}`;
    }

    function extractAtlasPageNames(atlasText) {
        const pages = [];
        const seen = new Set();
        const lines = String(atlasText || '').replace(/^\uFEFF/, '').split(/\r?\n/);
        for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line || line.includes(':') || !/\.png$/i.test(line)) continue;
            const key = line.toLocaleLowerCase();
            if (seen.has(key)) continue;
            seen.add(key);
            pages.push(line);
        }
        return pages;
    }

    function runtimeSymbols(runtime) {
        return REQUIRED_RUNTIME_SYMBOLS.filter((name) => typeof runtime?.[name] !== 'function');
    }

    function spineVersionFamily(value) {
        const match = String(value || '').match(/^(\d+)\.(\d+)/);
        return match ? `${match[1]}.${match[2]}` : '';
    }



    function getRuntime(options = {}) {
        return options.runtime || root?.spine || null;
    }

    function finiteOr(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    async function ensureRuntime(options = {}) {
        let runtime = getRuntime(options);
        const loader = options.runtimeLoader || root?.NekoSpineRuntime;
        if (runtime && runtimeSymbols(runtime).length === 0) return runtime;
        if (!runtime && loader && typeof loader.load === 'function') {
            await loader.load(options.runtimeLoadOptions || {});
            runtime = getRuntime(options);
        }
        const missing = runtimeSymbols(runtime);
        if (missing.length) {
            throw new SpineRendererError(
                `Spine WebGL runtime 不可用：${missing.join(', ')}`,
                'runtime_unavailable',
                { missing },
            );
        }
        return runtime;
    }

    function modelFromManifest(manifest) {
        if (!manifest || typeof manifest !== 'object') {
            throw new SpineRendererError('缺少模板 manifest', 'invalid_manifest');
        }
        const model = manifest.model;
        const skeleton = model?.skeleton;
        const atlas = model?.atlas;
        const skeletonPath = normalizePath(skeleton?.path);
        const atlasPath = normalizePath(atlas?.path);
        if (!skeletonPath || !atlasPath) {
            throw new SpineRendererError(
                'manifest 缺少 model.skeleton.path 或 model.atlas.path',
                'invalid_manifest',
            );
        }
        return { model, skeleton, atlas, skeletonPath, atlasPath };
    }

    async function openStoreAsset(store, requestedPath, mode = 'bytes') {
        if (!store || typeof store !== 'object') {
            throw new SpineRendererError('缺少临时模板资源仓库', 'invalid_asset_store');
        }
        const normalized = normalizePath(requestedPath);
        if (!normalized) throw new SpineRendererError('模板资源路径不安全', 'unsafe_path');

        const candidates = [normalized];
        const requestedBase = basename(normalized).toLocaleLowerCase();
        if (typeof store.list === 'function') {
            let listed = [];
            try { listed = store.list(); } catch (_) { listed = []; }
            const matches = listed.filter((path) => basename(path).toLocaleLowerCase() === requestedBase);
            if (matches.length === 1 && matches[0] !== normalized) candidates.push(matches[0]);
        }

        let lastError = null;
        for (const candidate of candidates) {
            try {
                if (typeof store.open === 'function') {
                    const value = await store.open(candidate, { as: mode });
                    if (mode === 'text') {
                        if (typeof value === 'string') return value;
                        return new TextDecoder().decode(toBytes(value));
                    }
                    return toBytes(value);
                }
                if (typeof store.get === 'function') {
                    const value = store.get(candidate);
                    if (value != null) {
                        return mode === 'text'
                            ? new TextDecoder().decode(toBytes(value))
                            : toBytes(value);
                    }
                }
            } catch (error) {
                lastError = error;
            }
        }
        throw new SpineRendererError(
            `模板资源不存在：${requestedPath}`,
            'asset_not_found',
            { path: requestedPath, cause: lastError },
        );
    }

    function clearColor(gl, color) {
        if (!gl || typeof gl.clearColor !== 'function' || typeof gl.clear !== 'function') return;
        const [r, g, b, a] = Array.isArray(color) ? color : DEFAULT_BACKGROUND;
        gl.clearColor(
            Number.isFinite(Number(r)) ? Number(r) : 0,
            Number.isFinite(Number(g)) ? Number(g) : 0,
            Number.isFinite(Number(b)) ? Number(b) : 0,
            Number.isFinite(Number(a)) ? Number(a) : 0,
        );
        gl.clear(gl.COLOR_BUFFER_BIT);
    }

    class SpineRenderer {
        constructor(options = {}) {
            if (!options.canvas || typeof options.canvas.getContext !== 'function') {
                throw new SpineRendererError('需要可获取 WebGL 上下文的 canvas', 'invalid_canvas');
            }
            this.canvas = options.canvas;
            this.manifest = options.manifest;
            this.assetStore = options.assetStore;
            this.options = { ...options };
            // Keep logical dimensions stable when DPR enlarges the backing bitmap.
            this.options.renderWidth = asPositiveNumber(options.renderWidth,
                asPositiveNumber(options.width, asPositiveNumber(this.canvas.width, 1024)));
            this.options.renderHeight = asPositiveNumber(options.renderHeight,
                asPositiveNumber(options.height, asPositiveNumber(this.canvas.height, this.options.renderWidth)));
            this.runtime = null;
            this.context = null;
            this.gl = null;
            this.renderer = null;
            this.assetManager = null;
            this.atlas = null;
            this.skeletonData = null;
            this.skeleton = null;
            this.stateData = null;
            this.state = null;
            this.currentAnimation = null;
            this.playing = false;
            this.loaded = false;
            this.disposed = false;
            this.warnings = [];
            this._modelInfo = null;
            this._characterSkinSelections = [];
            this._characterSlotStyles = [];
            this._characterSliderValues = [];
            this._baseFit = null;
            this._referenceScale = 1;
            this._viewportBounds = null;
            this._scene = null;
            this._sceneTransform = { x: 0, y: 0, rotation: 0, scale: 1 };
            // Spine 4.3 的 Skeleton 没有 rotation。编辑器角度加在根骨骼上，
            // 这个数记下动画自己的角度，避免每帧再叠一次。
            this._rootRotationBasis = null;
            this._editorRotationApplied = 0;
            this._colorMode = { mode: 'default', primary: null, secondary: null };
            this._background = [...DEFAULT_BACKGROUND];
            this._layerTextures = new Map();
            this._tintReferences = new WeakMap();
        }

        static async create(options = {}) {
            const instance = new SpineRenderer(options);
            await instance.load();
            return instance;
        }

        _assertLive() {
            if (this.disposed) throw new SpineRendererError('Spine renderer 已释放', 'released');
        }

        async load() {
            this._assertLive();
            if (this.loaded) return this;
            this._modelInfo = modelFromManifest(this.manifest);
            this.runtime = await ensureRuntime(this.options);

            const expectedVersion = this.runtime?.version || DEFAULT_RUNTIME_VERSION;
            const packageVersion = this.manifest?.source?.spineVersion
                || this._modelInfo.skeleton?.spineVersion
                || null;
            const sameVersionFamily = packageVersion && expectedVersion
                && spineVersionFamily(packageVersion) === spineVersionFamily(expectedVersion);
            if (packageVersion && expectedVersion && !sameVersionFamily) {
                const warning = `Spine 模型版本 ${packageVersion} 与运行时 ${expectedVersion} 不同`;
                this.warnings.push(warning);
                if (this.options.allowVersionMismatch !== true) {
                    throw new SpineRendererError(warning, 'runtime_version_mismatch', {
                        packageVersion,
                        runtimeVersion: expectedVersion,
                    });
                }
            }

            const webglConfig = {
                alpha: true,
                // Spine 4.3 只有一个混合模式：src=ONE, dst=ONE_MINUS_SRC_ALPHA，配合
                // `gl_FragColor = v_color * texture2D(...)` 的 shader，帧缓冲里存的是**预乘**
                // alpha。若声明成 false，浏览器会把这个 canvas 合成到导出用的 2D canvas 时
                // 再乘一次 alpha，半透明像素和柔边被二次压暗 —— 屏幕上预览正常（不走这次
                // 合成），只有导出的文件偏暗，所以别把它"修"回 false。
                premultipliedAlpha: true,
                // The editor exports directly from this canvas.  Without a
                // preserved drawing buffer Chromium is allowed to discard the
                // composited frame before toBlob()/toDataURL() reads it,
                // producing a fully transparent PNG even though the preview
                // is visibly rendered.
                preserveDrawingBuffer: true,
                ...(this.options.webglConfig || {}),
            };
            try {
                this.context = new this.runtime.ManagedWebGLRenderingContext(this.canvas, webglConfig);
                this.gl = this.context.gl;
                if (!this.gl) throw new Error('WebGL context creation failed');
                this.renderer = new this.runtime.SceneRenderer(
                    this.canvas,
                    this.context,
                    this.options.twoColorTint !== false,
                );
                if (this.options.matchTintPalette && this.renderer.twoColorTint) this._installPaletteShader();
                this.assetManager = new this.runtime.AssetManager(this.context);

                const atlasText = await openStoreAsset(this.assetStore, this._modelInfo.atlasPath, 'text');
                const atlasUri = bytesToDataUri(utf8Bytes(atlasText), 'text/plain;charset=utf-8');
                this.assetManager.setRawDataURI(this._modelInfo.atlasPath, atlasUri);
                // The pinned downloader's base64 text fast path uses atob without
                // UTF-8 decoding. Hand the already-decoded atlas to its parser.
                const downloader = this.assetManager.downloader;
                if (downloader?.downloadText) {
                    const downloadText = downloader.downloadText.bind(downloader);
                    downloader.downloadText = (path, success, failure) => {
                        if (path === this._modelInfo.atlasPath) success(atlasText);
                        else downloadText(path, success, failure);
                    };
                }

                const manifestPages = Array.isArray(this._modelInfo.atlas.pages)
                    ? this._modelInfo.atlas.pages
                    : [];
                const pageNames = manifestPages.length
                    ? manifestPages.map((page) => String(page?.name || '')).filter(Boolean)
                    : extractAtlasPageNames(atlasText);
                if (this.manifest.source?.builtIn === true && manifestPages.length) {
                    pageNames.push(...extractAtlasPageNames(atlasText)
                        .filter((name) => /^generated-[A-Za-z0-9_]+--[A-Za-z0-9_]+\.png$/.test(name)));
                }
                const atlasRoot = dirname(this._modelInfo.atlasPath);
                for (const page of manifestPages) {
                    const pageName = String(page?.name || '');
                    if (!pageName) continue;
                    const pagePath = page?.path || joinPath(atlasRoot, pageName);
                    const bytes = await openStoreAsset(this.assetStore, pagePath, 'bytes');
                    this._registerRawPage(pageName, pagePath, atlasRoot, bytes);
                }
                // Resolve pages added to built-ins and pages inferred for older manifests.
                for (const pageName of pageNames) {
                    const expectedPath = joinPath(atlasRoot, pageName);
                    if (this._hasRawPage(expectedPath)) continue;
                    const bytes = await openStoreAsset(this.assetStore, expectedPath, 'bytes');
                    this._registerRawPage(pageName, expectedPath, atlasRoot, bytes);
                }

                this.atlas = await this.assetManager.loadTextureAtlasAsync(this._modelInfo.atlasPath);
                const attachmentLoader = new this.runtime.AtlasAttachmentLoader(this.atlas);
                const skeletonBytes = await openStoreAsset(
                    this.assetStore,
                    this._modelInfo.skeletonPath,
                    'bytes',
                );
                const parser = /\.json$/i.test(this._modelInfo.skeletonPath)
                    ? new this.runtime.SkeletonJson(attachmentLoader)
                    : new this.runtime.SkeletonBinary(attachmentLoader);
                if ('scale' in parser && this.options.scale != null) {
                    parser.scale = Number(this.options.scale) || 1;
                }
                const parsedInput = parser instanceof this.runtime.SkeletonJson
                    ? new TextDecoder().decode(skeletonBytes)
                    : skeletonBytes;
                this.skeletonData = parser.readSkeletonData(parsedInput);
                this.skeleton = new this.runtime.Skeleton(this.skeletonData);
                this._bindNativeParts();
                this._referenceScale = asPositiveNumber(this.skeletonData?.referenceScale, 1);
                this._characterSkinSelections = this._readCharacterSkinSelections();
                if (!this._characterSkinSelections.length && this.skeletonData.skins?.length) {
                    const skin = this.skeletonData.defaultSkin || this.skeletonData.skins[0];
                    this._characterSkinSelections = [{ group: 'skin', value: skin.name }];
                }
                this._characterSlotStyles = this._readCharacterSlotStyles();
                this._characterSliderValues = this._readCharacterSliderValues();
                this._applyCharacterSkins();
                this.stateData = new this.runtime.AnimationStateData(this.skeletonData);
                this.state = new this.runtime.AnimationState(this.stateData);
                this.skeleton.updateWorldTransform(this.runtime.Physics?.update ?? 2);
                this._viewportBounds = null;
                this.loaded = true;

                const availableAnimations = this.animationNames();
                const requestedInitialAnimation = this.options.animationName
                    || this.manifest?.scenes?.[0]?.animation?.id
                    || this.manifest?.scenes?.[0]?.animationName
                    || availableAnimations[0];
                // Scene lists can outlive a skeleton revision.  Prefer the
                // requested scene when it exists, but keep the model usable
                // when that metadata points at a removed animation.
                const initialAnimation = availableAnimations.includes(requestedInitialAnimation)
                    ? requestedInitialAnimation
                    : availableAnimations[0];
                if (initialAnimation) {
                    this.selectAnimation(initialAnimation, {
                        loop: this.options.loop === true,
                        time: Number(this.options.time) || 0,
                        render: false,
                    });
                } else {
                    this._fitSkeleton();
                }
                if (this.options.renderOnLoad !== false) this.render();
                return this;
            } catch (error) {
                this.dispose();
                if (error instanceof SpineRendererError) throw error;
                throw new SpineRendererError(
                    `Spine 模板加载失败：${error?.message || error}`,
                    'load_failed',
                    { cause: error },
                );
            }
        }

        _rawPages = new Set();

        _registerRawPage(pageName, actualPath, atlasRoot, bytes) {
            const uri = bytesToDataUri(bytes, 'image/png');
            const expectedPath = joinPath(atlasRoot, pageName);
            this.assetManager.setRawDataURI(expectedPath, uri);
            const runtimePath = `${atlasRoot}${pageName}`;
            this.assetManager.setRawDataURI(runtimePath, uri);
            this.assetManager.setRawDataURI(pageName, uri);
            if (actualPath) this.assetManager.setRawDataURI(actualPath, uri);
            this._rawPages.add(expectedPath);
        }

        _hasRawPage(path) {
            return this._rawPages.has(normalizePath(path));
        }

        animationNames() {
            return this.skeletonData?.animations?.map((animation) => animation.name) || [];
        }

        _readCharacterSkinSelections() {
            return Array.isArray(this.manifest?.character?.skins) ? this.manifest.character.skins : [];
        }
        _readCharacterSlotStyles() {
            return Array.isArray(this.manifest?.character?.slots) ? this.manifest.character.slots : [];
        }
        _readCharacterSliderValues() { return []; }
        _bindNativeParts() {
            this._nativeParts = new Map();
            for (const part of this.manifest?.catalogs?.slotGroups || []) {
                const slot = this.skeleton.findSlot(part.id);
                const hidden = part.hideSlots.map((name) => this.skeleton.findSlot(name));
                if (!slot || hidden.some((item) => !item)
                    || part.attachments.some((name) => !this.skeletonData.skins.some(
                        (skin) => skin.getAttachment(slot.data.index, name)))) {
                    throw new SpineRendererError(`模板部件引用无效：${part.id}`, 'invalid_part_binding');
                }
                this._nativeParts.set(part.id, { ...part, slot, hidden });
            }
        }
        _applyCharacterSkins(selections = this._characterSkinSelections) {
            if (!this.skeleton || !this.skeletonData) return;
            const name = selections[0]?.value;
            if (name == null) return;
            const skin = this.skeletonData.findSkin?.(String(name));
            if (!skin) throw new SpineRendererError('Spine skin not found: ' + name, 'skin_not_found');
            this.skeleton.setSkin(skin);
            this.skeleton.setupPoseSlots?.();
            this._characterSkinCount = 1;
        }
        _restoreCharacterAttachments() {
            for (const [pose, saved] of this._attachmentBaselines || []) {
                pose.setAttachment(saved.attachment);
                pose.deform.length = 0;
                for (const value of saved.deform) pose.deform.push(value);
                pose.sequenceIndex = saved.sequenceIndex;
            }
            this._attachmentBaselines?.clear();
        }

        _applyCharacterSlotStyles() {
            const changeAttachment = (slot, attachment) => {
                const pose = slot.pose;
                if (pose.attachment === attachment) return;
                this._attachmentBaselines ||= new Map();
                if (!this._attachmentBaselines.has(pose)) {
                    this._attachmentBaselines.set(pose, {
                        attachment: pose.attachment, deform: [...pose.deform], sequenceIndex: pose.sequenceIndex,
                    });
                }
                pose.setAttachment(attachment);
            };
            for (const style of this._characterSlotStyles || []) {
                const part = this._nativeParts?.get(style.name);
                if (!part) continue;
                const { slot } = part;
                if (part.kind === 'toggle' && style.attachmentMode === 'hide') {
                    changeAttachment(slot, null);
                } else if (part.kind === 'toggle' && style.attachmentMode === 'show') {
                    const attachment = slot.pose.attachment || this.skeleton.getAttachment(slot.data.index, part.attachments[0]);
                    if (attachment) changeAttachment(slot, attachment);
                } else if (part.kind === 'choice' && style.attachmentMode === 'replace'
                    && slot.pose.attachment && part.attachments.includes(style.attachment)) {
                    const attachment = this.skeleton.getAttachment(slot.data.index, style.attachment);
                    if (attachment) changeAttachment(slot, attachment);
                }
                for (const color of new Set(this._slotColorPoses(slot).map((pose) => pose.color))) {
                    if (!color || !style.tintColor) continue;
                    const tint = this._colorObject(style.tintColor);
                    this._setSlotColor(color, { r: color.r * tint.r, g: color.g * tint.g, b: color.b * tint.b, a: color.a });
                }
                for (const color of new Set(this._slotColorPoses(slot).map((pose) => pose.darkColor))) {
                    if (color && style.tintBlackColor) this._setSlotColor(color, this._colorObject(style.tintBlackColor));
                }
            }
            for (const part of this._nativeParts?.values() || []) {
                if (part.slot.pose.attachment) {
                    for (const slot of part.hidden) changeAttachment(slot, null);
                }
            }
            this._applyGlobalColorMode();
        }

        _setSlotColor(color, value) {
            this._slotColorBaselines ||= new Map();
            if (!this._slotColorBaselines.has(color)) {
                this._slotColorBaselines.set(color, { r: color.r, g: color.g, b: color.b, a: color.a });
            }
            color.set(value.r, value.g, value.b, value.a);
        }

        partHasDarkTint(name) {
            return Boolean(this._nativeParts?.get(name)?.slot.pose.darkColor);
        }

        partIsVisible(name) {
            const slot = this._nativeParts.get(name)?.slot;
            if (!slot?.bone.active || this.skeleton?.visible === false) return false;
            const { RegionAttachment, MeshAttachment } = this.runtime;
            const { attachment, color } = slot.appliedPose;
            return color.a > 0 && attachment?.color?.a > 0
                && (attachment instanceof RegionAttachment || attachment instanceof MeshAttachment);
        }

        visiblePartNames() {
            return Array.from(this._nativeParts.keys()).filter((name) => this.partIsVisible(name));
        }

        _installPaletteShader() {
            const original = this.renderer.batcherShader;
            const source = original.getFragmentSource();
            const insertion = 'gl_FragColor.a = texColor.a * v_light.a;';
            if (!source.includes(insertion)) return;
            // Use high precision for texel centers on large accessory textures when the GPU supports it.
            const samplePrecision = this.gl.getShaderPrecisionFormat(this.gl.FRAGMENT_SHADER, this.gl.HIGH_FLOAT)?.precision
                ? 'highp' : 'mediump';
            const fragment = source.replace('void main () {', `
                vec4 cubicWeights(float x) {
                    float x2 = x * x;
                    float x3 = x2 * x;
                    return vec4(-0.5 * x + x2 - 0.5 * x3, 1.0 - 2.5 * x2 + 1.5 * x3,
                        0.5 * x + 2.0 * x2 - 1.5 * x3, -0.5 * x2 + 0.5 * x3);
                }

                vec4 sampleAccessory(${samplePrecision} vec2 uv, ${samplePrecision} vec2 size) {
                    ${samplePrecision} vec2 p = uv * size - 0.5;
                    ${samplePrecision} vec2 center = floor(p) + 0.5;
                    vec4 wx = cubicWeights(fract(p.x));
                    vec4 wy = cubicWeights(fract(p.y));
                    vec4 sampled = vec4(0.0);
                    for (int y = 0; y < 4; y++) {
                        for (int x = 0; x < 4; x++) {
                            vec2 offset = vec2(float(x - 1), float(y - 1));
                            sampled += texture2D(u_texture, (center + offset) / size) * wx[x] * wy[y];
                        }
                    }
                    sampled.a = clamp(sampled.a, 0.0, 1.0);
                    return vec4(clamp(sampled.rgb, vec3(0.0), vec3(sampled.a)), sampled.a);
                }

                void main () {
                    if (v_dark.a > 2.5) {
                        gl_FragColor = sampleAccessory(v_texCoords, v_dark.rg);
                        return;
                    }
            `).replace(insertion, `
                ${insertion}
                bool pulse = abs(v_dark.a) > 1.0;
                if (v_dark.a < 0.0) {
                    float luminance = dot(texColor.rgb, vec3(${LUMINANCE.join(', ')})) / max(texColor.a, 0.0001);
                    float base = fract(-v_dark.a);
                    vec3 shadow = v_dark.rgb;
                    vec3 highlight = vec3(v_light.a);
                    vec3 shaded = luminance < base
                        ? mix(shadow, v_light.rgb, luminance / base)
                        : mix(v_light.rgb, highlight, clamp((luminance - base) / (1.0 - base), 0.0, 1.0));
                    gl_FragColor.rgb = shaded * texColor.a;
                } else if (pulse) {
                    gl_FragColor.rgb = (texColor.a - texColor.rgb) * v_dark.rgb + texColor.rgb * v_light.rgb;
                }
                if (pulse) gl_FragColor.rgb = mix(gl_FragColor.rgb, vec3(0.36, 0.83, 1.0) * gl_FragColor.a, 0.56);
                if (pulse || v_dark.a < 0.0) return;
            `);
            const shader = new this.runtime.Shader(this.context, original.getVertexShaderSource(), fragment);
            original.dispose();
            this.renderer.batcherShader = shader;
            this._paletteTint = true;
        }

        _attachmentTintReference(pose) {
            const sequence = pose.attachment?.sequence;
            const region = sequence?.regions[sequence.resolveIndex(pose)];
            if (!region) return 0.7;
            if (this._tintReferences.has(region)) return this._tintReferences.get(region);
            const texture = region.texture;
            const image = texture?.getImage();
            if (!image?.width || !image.height) return 0.7;
            this._tintSample ||= this.canvas.ownerDocument.createElement('canvas');
            const sample = this._tintSample;
            sample.width = sample.height = 48;
            const context = sample.getContext('2d', { willReadFrequently: true });
            const { u, v, u2, v2 } = region;
            context.drawImage(image, u * image.width, v * image.height, (u2 - u) * image.width, (v2 - v) * image.height,
                0, 0, sample.width, sample.height);
            const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
            const histogram = Array(256).fill(0);
            let peak = 0;
            for (let i = 0; i < pixels.length; i += 4) {
                const alpha = pixels[i + 3] / 255;
                if (alpha < 0.85) continue;
                const luma = pixels[i] * LUMINANCE[0] + pixels[i + 1] * LUMINANCE[1] + pixels[i + 2] * LUMINANCE[2];
                const bucket = Math.min(255, Math.round(luma / (texture.pma ? alpha : 1)));
                histogram[bucket] += 1;
                if (histogram[bucket] > histogram[peak]) peak = bucket;
            }
            const base = Math.max(0.004, Math.min(0.996, histogram[peak] ? peak / 255 : 0.7));
            this._tintReferences.set(region, base);
            return base;
        }

        _paletteVertexTransform(highlightedPart) {
            if (!this._paletteTint) return {};
            const restore = [];
            const tints = new Map(this._characterSlotStyles.filter((style) => style.tintColor)
                .map((style) => [style.name, style.tintColor]));
            if (highlightedPart && !tints.has(highlightedPart)) tints.set(highlightedPart, null);
            for (const [name, tintColor] of tints) {
                const slot = this._nativeParts.get(name)?.slot;
                const pose = slot?.appliedPose;
                const attachment = pose?.attachment;
                if (!attachment?.sequence || slot.data.blendMode === 1) continue;
                const alpha = this.skeleton.color.a * pose.color.a * attachment.color.a;
                if (alpha <= 0) continue;
                const shadow = pose.darkColor;
                const base = tintColor && this._attachmentTintReference(pose);
                const tint = tintColor && this._colorObject(tintColor);
                restore.push({ pose, shadow, base, tint, pulse: name === highlightedPart });
                // The runtime's vertex callback receives colors after premultiplication.
                // Tag this slot there so untinted slots keep the standard Spine shader path.
                pose.darkColor = { r: -restore.length, g: shadow?.g || 0, b: shadow?.b || 0 };
            }
            if (!restore.length) return {};
            return {
                transform: (vertices, length, stride) => {
                    for (let index = 0; index < length; index += stride) {
                        const light = index + 2;
                        const dark = index + 8;
                        if (vertices[dark] >= 0) continue;
                        const alpha = vertices[light + 3];
                        const tag = Math.round(-vertices[dark] / alpha);
                        const { shadow, base, tint, pulse } = restore[tag - 1];
                        if (tint) {
                            vertices[light] = tint.r * alpha;
                            vertices[light + 1] = tint.g * alpha;
                            vertices[light + 2] = tint.b * alpha;
                        }
                        vertices[dark] = (shadow?.r || 0) * alpha;
                        vertices[dark + 3] = tint ? -(base + Number(pulse)) : 2;
                    }
                },
                reset: () => {
                    for (const { pose, shadow } of restore) pose.darkColor = shadow;
                },
            };
        }

        _slotColorPoses(slot) {
            return [...new Set([slot?.pose, slot?.appliedPose, slot])]
                .filter((pose) => pose && (pose.color || pose.darkColor));
        }

        _restoreSlotColors() {
            for (const [color, value] of this._slotColorBaselines || []) {
                color.set(value.r, value.g, value.b, value.a);
            }
            this._slotColorBaselines = new Map();
        }

        _colorObject(value, fallback = { r: 1, g: 1, b: 1, a: 1 }) {
            if (!value || typeof value !== 'object') return fallback;
            return {
                r: Math.max(0, Math.min(1, finiteOr(value.r, fallback.r))),
                g: Math.max(0, Math.min(1, finiteOr(value.g, fallback.g))),
                b: Math.max(0, Math.min(1, finiteOr(value.b, fallback.b))),
                a: Math.max(0, Math.min(1, finiteOr(value.a, fallback.a))),
            };
        }

        _applyGlobalColorMode() {
            const mode = this._colorMode || {};
            if (mode.mode === 'default' || !this.skeleton?.slots) return;
            const primary = this._colorObject(mode.primary);
            const secondary = this._colorObject(mode.secondary, { r: 0, g: 0, b: 0, a: 1 });
            const applied = new Set();
            const tint = (color, value) => {
                if (!color?.set || applied.has(color)) return;
                applied.add(color);
                this._setSlotColor(color, value);
            };
            for (const slot of this.skeleton.slots) {
                for (const pose of this._slotColorPoses(slot)) {
                    if (pose.color?.set) {
                        tint(pose.color, {
                            r: mode.mode === 'tint' ? primary.r : pose.color.r * primary.r,
                            g: mode.mode === 'tint' ? primary.g : pose.color.g * primary.g,
                            b: mode.mode === 'tint' ? primary.b : pose.color.b * primary.b,
                            a: pose.color.a * primary.a,
                        });
                    }
                    if (mode.mode === 'two-tone' && pose.darkColor?.set) {
                        tint(pose.darkColor, secondary);
                    }
                }
            }
        }

        skinOptions() {
            const options = (this.skeletonData?.skins || []).map((skin) => skin.name);
            return options.length ? { skin: { group: 'skin', options } } : {};
        }
        setCharacterState(next = {}, options = {}) {
            let changed = false;
            if (Array.isArray(next.skins)) {
                const selection = next.skins.map((item) => ({ ...item }));
                if (JSON.stringify(selection) !== JSON.stringify(this._characterSkinSelections)) {
                    this._characterSkinSelections = selection;
                    this._restoreCharacterAttachments();
                    this._applyCharacterSkins();
                    changed = true;
                }
            }
            if (Array.isArray(next.slots) && JSON.stringify(next.slots) !== JSON.stringify(this._characterSlotStyles)) {
                this._characterSlotStyles = next.slots.map((item) => ({ ...item }));
                changed = true;
            }
            if (changed) this._applyPose(0);
            if (options.render !== false) this.render();
            return this.status();
        }

        animations() {
            return this.skeletonData?.animations?.map((animation) => ({
                name: animation.name,
                duration: Number(animation.duration) || 0,
            })) || [];
        }

        hasPhysicsConstraints() {
            const PhysicsConstraint = this.runtime?.PhysicsConstraint;
            return Boolean(PhysicsConstraint && this.skeleton?.constraints?.some(
                (constraint) => constraint instanceof PhysicsConstraint,
            ));
        }

        selectAnimation(name, options = {}) {
            this._assertLive();
            const animation = this.skeletonData?.findAnimation(String(name));
            if (!animation) throw new SpineRendererError('Spine animation not found: ' + name, 'animation_not_found');
            this._restoreSlotColors();
            this._restoreCharacterAttachments();
            this.state.clearTracks();
            this.skeleton.setupPose?.();
            this._rootRotationBasis = null;
            this._editorRotationApplied = 0;
            this._applyCharacterSkins();
            this.skeleton.time = 0;
            const entry = this.state.setAnimation(0, String(name), options.loop !== false);
            this.currentAnimation = String(name);
            this.playing = options.autoplay === true;
            this.state.apply(this.skeleton);
            this._applyCharacterSlotStyles();
            this.skeleton.updateWorldTransform(this.runtime.Physics?.reset ?? 1);
            this._captureRootRotationBasis();
            if (Number(options.time) > 0) this.setTime(Number(options.time), { render: false });
            if (options.render !== false) this.render();
            return { name: String(name), duration: Number(animation.duration) || 0, time: entry.trackTime, playing: this.playing };
        }
        setTime(time, options = {}) {
            this._assertLive();
            let entry = this.state?.getTrack?.(0);
            if (!entry) throw new SpineRendererError('尚未选择 Spine 动画', 'animation_not_selected');
            let target = Math.max(0, Number(time) || 0);
            if (!entry.loop) target = Math.min(target, Number(entry.animation?.duration) || 0);
            if (target < entry.trackTime) {
                this.selectAnimation(this.currentAnimation, { loop: entry.loop, autoplay: this.playing, render: false });
                entry = this.state.getTrack(0);
            }
            const distance = target - entry.trackTime;
            if (distance > 600) throw new SpineRendererError('动画跳转超过模拟预算', 'seek_limit');
            let remaining = distance;
            while (remaining > 1e-8) {
                const step = Math.min(remaining, 1 / 60);
                this._applyPose(step);
                remaining -= step;
            }
            if (options.render !== false) this.render();
            return target;
        }

        play(name, options = {}) {
            this._assertLive();
            if (name != null && name !== this.currentAnimation) {
                this.selectAnimation(name, { ...options, autoplay: true });
            } else {
                this.playing = true;
            }
            return this.playing;
        }

        pause() {
            this._assertLive();
            this.playing = false;
            return this.playing;
        }

        _applyPose(deltaSeconds) {
            if (!this.state || !this.skeleton) return;
            // 先把上一帧加上的编辑器旋转拿掉，动画混合才不会拿那个角度当起点。
            this._restoreRootRotationBasis();
            this._restoreSlotColors();
            this._restoreCharacterAttachments();
            if (deltaSeconds > 0) {
                this.skeleton.update(deltaSeconds);
                this.state.update(deltaSeconds);
            }
            this.state.apply(this.skeleton);
            this._applyCharacterSlotStyles();
            this.skeleton.updateWorldTransform(this.runtime.Physics?.update ?? 2);
            this._captureRootRotationBasis();
        }

        _rootAppliedPose() {
            const root = this.skeleton?.getRootBone?.();
            if (!root) return null;
            return root.appliedPose || root.pose || null;
        }

        _captureRootRotationBasis() {
            const pose = this._rootAppliedPose();
            if (!pose || typeof pose.rotation !== 'number') return;
            this._rootRotationBasis = pose.rotation;
        }

        _restoreRootRotationBasis() {
            const pose = this._rootAppliedPose();
            if (!pose || typeof this._rootRotationBasis !== 'number') return;
            pose.rotation = this._rootRotationBasis;
        }

        // Skeleton.x / scaleX 会进绘制，rotation 不会。角度加在根骨骼的本地旋转上，
        // 整棵骨架绕根骨转。同一帧里相机会套两次，第二次必须还是同一个角度。
        _applyEditorRotation() {
            const degrees = finiteOr(this._sceneTransform?.rotation, 0);
            if (this.skeleton) this.skeleton.rotation = degrees;
            const pose = this._rootAppliedPose();
            if (!pose || typeof pose.rotation !== 'number'
                || typeof this.skeleton.updateWorldTransform !== 'function') return;
            if (typeof this._rootRotationBasis !== 'number') {
                if (degrees === 0) return;
                this._rootRotationBasis = pose.rotation;
            }
            const next = this._rootRotationBasis + degrees;
            if (pose.rotation === next && this._editorRotationApplied === degrees) return;
            pose.rotation = next;
            this._editorRotationApplied = degrees;
            this.skeleton.updateWorldTransform(this.runtime?.Physics?.pose ?? 3);
        }

        tick(deltaSeconds = 1 / 60, options = {}) {
            this._assertLive();
            if (!this.loaded) throw new SpineRendererError('Spine 模板尚未加载', 'not_loaded');
            const delta = Math.max(0, Math.min(Number(deltaSeconds) || 0, 0.25));
            if (this.playing) this._applyPose(delta);
            if (options.render !== false) this.render();
            const entry = this.state?.getTrack?.(0);
            return Object.freeze({
                animation: this.currentAnimation,
                time: Number(entry?.trackTime) || 0,
                playing: this.playing,
                delta,
            });
        }

        _resizeCanvas() {
            // The canvas is responsive through CSS. Do not derive its
            // intrinsic bitmap from clientWidth/clientHeight: the editor canvas
            // and its PNG export are square. Resizing from CSS dimensions
            // would silently turn a 1024x1024 export into a wide thumbnail.
            const renderWidth = asPositiveNumber(
                this.options.renderWidth,
                asPositiveNumber(this.options.width, asPositiveNumber(this.canvas.width, 1024)),
            );
            const renderHeight = asPositiveNumber(
                this.options.renderHeight,
                asPositiveNumber(this.options.height, asPositiveNumber(this.canvas.height, renderWidth)),
            );
            const dpr = asPositiveNumber(
                this.options.devicePixelRatio,
                1,
            );
            const width = Math.max(1, Math.round(renderWidth * dpr));
            const height = Math.max(1, Math.round(renderHeight * dpr));
            if (this.canvas.width !== width) this.canvas.width = width;
            if (this.canvas.height !== height) this.canvas.height = height;
            // Spine's SceneRenderer.resize() intentionally follows the CSS
            // client rectangle and would undo the square export dimensions
            // above. Drive its camera/viewport directly instead.
            if (this.renderer?.camera?.setViewport) {
                this.renderer.camera.setViewport(width, height);
                this.renderer.camera.update?.();
            } else if (typeof this.renderer?.resize === 'function') {
                // Compatibility fallback for an older runtime without an
                // exposed camera viewport API. Restore our bitmap dimensions
                // after its responsive resize call.
                this.renderer.resize(1);
                if (this.canvas.width !== width) this.canvas.width = width;
                if (this.canvas.height !== height) this.canvas.height = height;
            }
            if (typeof this.gl?.viewport === 'function') this.gl.viewport(0, 0, width, height);
            return { width, height };
        }



        _readDrawableBounds() {
            if (!this.skeleton || typeof this.skeleton.getBounds !== 'function') return null;
            const Vector2 = this.runtime?.Vector2;
            // Spine exposes Vector2, but a few embedders provide a compatible
            // Skeleton without exporting that constructor.  getBounds only
            // needs mutable x/y fields, so plain records are a safe fallback.
            const offset = typeof Vector2 === 'function' ? new Vector2(0, 0) : { x: 0, y: 0 };
            const size = typeof Vector2 === 'function' ? new Vector2(0, 0) : { x: 0, y: 0 };
            try {
                this.skeleton.getBounds(offset, size);
            } catch (_) {
                return null;
            }
            const minX = Number(offset.x);
            const minY = Number(offset.y);
            const width = Number(size.x);
            const height = Number(size.y);
            if (![minX, minY, width, height].every(Number.isFinite)
                || width <= 0 || height <= 0) return null;
            return {
                minX,
                minY,
                maxX: minX + width,
                maxY: minY + height,
                width,
                height,
                centerX: minX + width / 2,
                centerY: minY + height / 2,
                source: 'skeleton-bounds',
            };
        }



        _fitSkeleton() {
            if (!this.skeleton || !this.renderer?.camera || typeof this.skeleton.getBounds !== 'function') return;
            const viewportWidth = asPositiveNumber(this.renderer.camera.viewportWidth, 1);
            const viewportHeight = asPositiveNumber(this.renderer.camera.viewportHeight, 1);
            if (this._baseFit
                && this._baseFit.viewportWidth === viewportWidth
                && this._baseFit.viewportHeight === viewportHeight) {
                this.skeleton.x = this._baseFit.x;
                this.skeleton.y = this._baseFit.y;
                this.skeleton.scaleX = this._baseFit.scaleX;
                this.skeleton.scaleY = this._baseFit.scaleY;
                this.skeleton.rotation = this._baseFit.rotation;
                this.renderer.camera.zoom = this._baseFit.zoom;
                this._applySceneCamera();
                return;
            }
            // Measure the untransformed skeleton. Scene transforms and the
            // editor's adjustment controls are applied after this baseline.
            this.skeleton.x = 0;
            this.skeleton.y = 0;
            this.skeleton.scaleX = 1;
            this.skeleton.scaleY = 1;
            this.skeleton.rotation = 0;
            const bounds = this._viewportBounds || this._readDrawableBounds();
            const width = asPositiveNumber(bounds?.width, 1);
            const height = asPositiveNumber(bounds?.height, 1);
            const padding = asPositiveNumber(this.options.fitPadding, 1.1);
            const zoom = Math.max(width / viewportWidth, height / viewportHeight) * padding;
            this._baseFit = {
                viewportWidth,
                viewportHeight,
                x: -(Number(bounds?.minX || 0) + width / 2),
                y: -(Number(bounds?.minY || 0) + height / 2),
                scaleX: 1,
                scaleY: 1,
                rotation: 0,
                zoom,
                width,
                height,
                boundsSource: this._viewportBounds
                    ? 'viewport-bounding-box'
                    : (bounds ? 'skeleton-bounds' : 'fallback'),
            };
            this.skeleton.x = this._baseFit.x;
            this.skeleton.y = this._baseFit.y;
            this.renderer.camera.zoom = zoom;
            this._applySceneCamera();
        }

        _applySceneCamera() {
            if (!this._baseFit || !this.renderer?.camera) return;
            const fit = this._baseFit;
            const transform = this._sceneTransform || {};
            const scale = Math.max(0.01, finiteOr(transform.scale, 1));
            const mirror = this._scene?.model?.mirrored ? -1 : 1;
            const unit = fit.zoom * this.renderer.camera.viewportHeight / 1000;
            this.skeleton.x = finiteOr(transform.x, 0) * unit;
            this.skeleton.y = -finiteOr(transform.y, 0) * unit;
            this.skeleton.scaleX = mirror * scale;
            this.skeleton.scaleY = scale;
            this.skeleton.visible = this._scene?.model?.visible !== false;
            this.renderer.camera.zoom = fit.zoom;
            this.renderer.camera.position?.set?.(-fit.x * mirror * scale, -fit.y * scale, 0);
            this.renderer.camera.direction?.set?.(0, 0, -1);
            this.renderer.camera.up?.set?.(0, 1, 0);
            this._applyEditorRotation();
            this.skeleton.updateWorldTransform(this.runtime.Physics?.pose ?? 3);
            this.renderer.camera.update?.();
        }

        setScene(scene, options = {}) {
            this._assertLive();
            this._scene = scene && typeof scene === 'object' ? scene : null;
            if (options.render !== false && this.loaded) this.render();
            return this._scene;
        }

        boneNames() {
            return this.skeleton?.bones?.map((bone) => bone.data.name) || [];
        }

        drawOrderNames() {
            return this.skeleton?.drawOrder?.appliedPose?.map((slot) => slot.data.name) || [];
        }

        _drawWithImageLayers(slots, layers, vertexTransform) {
            const { GLTexture, RegionAttachment, Vector3 } = this.runtime;
            const { width, height } = this.canvas;
            const activeImages = new Set(layers.map((layer) => layer.image));
            for (const [image, layer] of this._layerTextures) {
                if (activeImages.has(image)) continue;
                layer.texture.dispose();
                this._layerTextures.delete(image);
            }
            if (!layers.length || !slots?.length) return false;

            const layersBySlot = new Map();
            for (const { image, aboveSlot, corners } of layers) {
                let layer = this._layerTextures.get(image);
                if (!layer) {
                    const isPowerOfTwo = (value) => (value & (value - 1)) === 0;
                    const mipmaps = typeof this.gl.texStorage2D === 'function'
                        || (isPowerOfTwo(image.naturalWidth) && isPowerOfTwo(image.naturalHeight));
                    const texture = new GLTexture(this.context, image, false, mipmaps);
                    const uv = [0, 0, 0, 1, 1, 1, 1, 0];
                    // A temporary region participates in the same Spine batch and draw order as the template.
                    const attachment = new RegionAttachment('wardrobe-image', {
                        resolveIndex: () => 0, getUVs: () => uv, regions: [{ texture }], offsets: [[]],
                    });
                    const points = Array.from({ length: 4 }, () => new Vector3());
                    attachment.computeWorldVertices = (_slot, _offsets, output, start, stride) => {
                        for (const point of points) {
                            output[start] = point.x;
                            output[start + 1] = point.y;
                            start += stride;
                        }
                    };
                    const slot = { bone: { active: true }, data: { blendMode: 0, index: -1 },
                        appliedPose: { attachment, color: attachment.color, darkColor: null } };
                    layer = { texture, points, slot };
                    this._layerTextures.set(image, layer);
                }
                for (const [index, [x, y]] of corners.entries()) {
                    const point = layer.points[index].set(x * width, y * height - 1, 0);
                    this.renderer.camera.screenToWorld(point, width, height);
                }
                const [[left, top], [bottomX, bottomY], , [rightX, rightY]] = corners;
                const magnified = Math.hypot((rightX - left) * width, (rightY - top) * height) > image.naturalWidth
                    && Math.hypot((bottomX - left) * width, (bottomY - top) * height) > image.naturalHeight;
                layer.slot.appliedPose.darkColor = this._paletteTint && magnified
                    ? { r: image.naturalWidth, g: image.naturalHeight, b: -1 } : null;
                const additions = layersBySlot.get(aboveSlot) || [];
                additions.push(layer.slot);
                layersBySlot.set(aboveSlot, additions);
            }
            const drawOrder = this.skeleton.drawOrder;
            drawOrder.appliedPose = slots.flatMap((slot) => [slot, ...(layersBySlot.get(slot.data.name) || [])]);
            try {
                this.renderer.drawSkeleton(this.skeleton, -1, -1, (vertices, length, stride) => {
                    vertexTransform?.(vertices, length, stride);
                    if (!this._paletteTint) return;
                    // A negative dark blue marks the accessory vertices; the shader uses RGB for other Spine dark colors.
                    for (let dark = 8; dark < length; dark += stride) {
                        if (vertices[dark + 2] !== -1 || vertices[dark + 3] !== 1) continue;
                        vertices[dark + 3] = 3;
                    }
                });
            } finally {
                drawOrder.appliedPose = slots;
            }
            return true;
        }

        boneCanvasTransform(name, referenceName = name) {
            const bone = this.skeleton?.findBone(name);
            const pose = bone?.appliedPose;
            const camera = this.renderer?.camera;
            const Vector3 = this.runtime?.Vector3;
            if (!bone?.active || !pose || !camera?.worldToScreen || !Vector3) return null;
            const { width, height } = this.canvas;
            const project = (x, y) => camera.worldToScreen(new Vector3(x, y, 0), width, height);
            const origin = project(pose.worldX, pose.worldY);
            // Keep the same fraction along the limb when a different bone takes over.
            const referenceLength = referenceName === name ? 0 : this.skeleton.findBone(referenceName)?.data.length;
            const along = referenceLength > 0 && bone.data.length > 0 ? bone.data.length / referenceLength : 1;
            const x = project(pose.worldX + pose.a * along, pose.worldY + pose.c * along);
            const y = project(pose.worldX + pose.b, pose.worldY + pose.d);
            const matrix = [(x.x - origin.x) / width, (origin.y - x.y) / height,
                (y.x - origin.x) / width, (origin.y - y.y) / height, origin.x / width, 1 - origin.y / height];
            return matrix.every(Number.isFinite) ? matrix : null;
        }

        attachmentsAtCanvasPoint(x, y) {
            const skeleton = this.skeleton;
            const camera = this.renderer?.camera;
            const { Vector3, MeshAttachment, RegionAttachment } = this.runtime || {};
            if (!skeleton || skeleton.visible === false || !camera?.screenToWorld
                    || !Vector3 || !MeshAttachment || !RegionAttachment) return [];
            const { width, height } = this.canvas;
            const point = camera.screenToWorld(new Vector3(x * width, y * height - 1, 0), width, height);
            const slots = skeleton.drawOrder.appliedPose;
            const hits = [];
            for (let index = slots.length - 1; index >= 0; index -= 1) {
                const slot = slots[index];
                const pose = slot.appliedPose;
                const attachment = pose.attachment;
                if (!slot.bone.active || !attachment || pose.color.a === 0 || attachment.color?.a === 0) continue;
                let vertices, triangles;
                if (attachment instanceof MeshAttachment) {
                    vertices = new Float32Array(attachment.worldVerticesLength);
                    attachment.computeWorldVertices(skeleton, slot, 0, vertices.length, vertices, 0, 2);
                    triangles = attachment.triangles;
                } else if (attachment instanceof RegionAttachment) {
                    vertices = new Float32Array(8);
                    attachment.computeWorldVertices(slot, attachment.getOffsets(pose), vertices, 0, 2);
                    triangles = QUAD_TRIANGLES;
                } else {
                    continue;
                }
                for (let i = 0; i < triangles.length; i += 3) {
                    const indices = [triangles[i], triangles[i + 1], triangles[i + 2]];
                    const weights = triangleWeights(vertices, indices, point.x, point.y);
                    if (!weights) continue;
                    const bone = meshBoneAt(attachment, indices, weights, skeleton.bones) || slot.bone;
                    hits.push({ bone: bone.data.name, slot: slot.data.name });
                    break;
                }
            }
            return hits;
        }

        setTransform(transform = {}, options = {}) {
            this._assertLive();
            this._sceneTransform = {
                x: finiteOr(transform.x, 0),
                y: finiteOr(transform.y, 0),
                rotation: finiteOr(transform.rotation, 0),
                scale: Math.max(0.01, finiteOr(transform.scale, 1)),
            };
            if (options.render !== false && this.loaded) this.render();
            return { ...this._sceneTransform };
        }

        setColorMode(colorMode = {}, options = {}) {
            this._assertLive();
            this._colorMode = {
                mode: String(colorMode.mode || 'default'),
                primary: colorMode.primary || null,
                secondary: colorMode.secondary || null,
            };
            if (this.loaded) this._applyPose(0);
            if (options.render !== false && this.loaded) this.render();
            return { ...this._colorMode };
        }

        setBackground(color, options = {}) {
            this._assertLive();
            this._background = Array.isArray(color) ? color.slice(0, 4) : [...DEFAULT_BACKGROUND];
            if (options.render !== false && this.loaded) this.render();
            return [...this._background];
        }

        render(options = {}) {
            this._assertLive();
            if (!this.loaded || !this.renderer || !this.gl) {
                throw new SpineRendererError('Spine 模板尚未加载', 'not_loaded');
            }
            const viewport = this._resizeCanvas();
            if (options.fit !== false && this.options.autoFit !== false) this._fitSkeleton();
            this._applySceneCamera();
            clearColor(this.gl, options.background || this._background || this.options.background || DEFAULT_BACKGROUND);
            const partial = Number.isFinite(options.startSlot) || Number.isFinite(options.endSlot);
            const layers = partial ? [] : (this.options.imageLayers?.() || []);
            const highlightedPart = options.highlight === false ? null : this.options.highlightedPart?.();
            const palette = this.skeleton.visible !== false ? this._paletteVertexTransform(highlightedPart) : {};
            this.renderer.begin();
            try {
                if (this.skeleton.visible !== false
                    && !this._drawWithImageLayers(this.skeleton.drawOrder?.appliedPose, layers, palette.transform)) {

                    this.renderer.drawSkeleton(
                        this.skeleton,
                        Number.isFinite(options.startSlot) ? options.startSlot : -1,
                        Number.isFinite(options.endSlot) ? options.endSlot : -1,
                        palette.transform,
                    );
                }
            } finally {
                palette.reset?.();
                this.renderer.end();
            }
            return Object.freeze({
                width: viewport.width,
                height: viewport.height,
                animation: this.currentAnimation,
                time: Number(this.state?.getTrack?.(0)?.trackTime) || 0,
                visible: this.skeleton.visible !== false,
            });
        }

        captureFrame(options = {}) {
            this._assertLive();
            if (!this.loaded || !this.gl || !this.canvas) {
                throw new SpineRendererError('Spine 模板尚未加载', 'not_loaded');
            }
            if (options.render !== false) this.render(options.renderOptions || {});
            const width = Math.max(1, Math.round(Number(this.canvas.width) || 0));
            const height = Math.max(1, Math.round(Number(this.canvas.height) || 0));
            if (typeof this.gl.readPixels !== 'function') {
                throw new SpineRendererError('当前 WebGL 运行时不支持读取帧像素', 'pixel_readback_unavailable');
            }
            const raw = new Uint8Array(width * height * 4);
            const rgba = this.gl.RGBA ?? 0x1908;
            const unsignedByte = this.gl.UNSIGNED_BYTE ?? 0x1401;
            this.gl.readPixels(0, 0, width, height, rgba, unsignedByte, raw);
            // WebGL's origin is bottom-left while ImageData/GIF use a
            // top-left origin.  Return a normal ImageData-like object so the
            // caller does not need to know which renderer produced the frame.
            const data = new Uint8ClampedArray(raw.length);
            const rowBytes = width * 4;
            for (let row = 0; row < height; row += 1) {
                const sourceOffset = (height - row - 1) * rowBytes;
                data.set(raw.subarray(sourceOffset, sourceOffset + rowBytes), row * rowBytes);
            }
            // The WebGL buffer is premultiplied; ImageData and PNG/GIF consumers
            // expect straight RGB, including partially transparent edge pixels.
            for (let index = 0; index < data.length; index += 4) {
                const alpha = data[index + 3];
                if (alpha > 0 && alpha < 255) {
                    for (let channel = 0; channel < 3; channel += 1) {
                        data[index + channel] = Math.min(255, Math.round(data[index + channel] * 255 / alpha));
                    }
                }
            }
            const maxDimension = Number(options.maxDimension);
            const requestedWidth = Number(options.width);
            const requestedHeight = Number(options.height);
            let outputWidth = Number.isFinite(requestedWidth) && requestedWidth > 0
                ? Math.round(requestedWidth)
                : width;
            let outputHeight = Number.isFinite(requestedHeight) && requestedHeight > 0
                ? Math.round(requestedHeight)
                : height;
            if (Number.isFinite(maxDimension) && maxDimension > 0) {
                const scale = Math.min(1, maxDimension / Math.max(outputWidth, outputHeight));
                outputWidth = Math.max(1, Math.round(outputWidth * scale));
                outputHeight = Math.max(1, Math.round(outputHeight * scale));
            }
            if (outputWidth === width && outputHeight === height) {
                return Object.freeze({ width, height, data });
            }
            const scaled = new Uint8ClampedArray(outputWidth * outputHeight * 4);
            for (let y = 0; y < outputHeight; y += 1) {
                const sourceY = Math.min(height - 1, Math.floor(y * height / outputHeight));
                for (let x = 0; x < outputWidth; x += 1) {
                    const sourceX = Math.min(width - 1, Math.floor(x * width / outputWidth));
                    const sourceOffset = (sourceY * width + sourceX) * 4;
                    const targetOffset = (y * outputWidth + x) * 4;
                    scaled[targetOffset] = data[sourceOffset];
                    scaled[targetOffset + 1] = data[sourceOffset + 1];
                    scaled[targetOffset + 2] = data[sourceOffset + 2];
                    scaled[targetOffset + 3] = data[sourceOffset + 3];
                }
            }
            return Object.freeze({ width: outputWidth, height: outputHeight, data: scaled });
        }

        status() {
            const runtimeVersion = this.runtime?.version
                || this.options.runtimeVersion
                || DEFAULT_RUNTIME_VERSION;
            return Object.freeze({
                loaded: this.loaded,
                disposed: this.disposed,
                playing: this.playing,
                animation: this.currentAnimation,
                runtimeVersion,
                packageVersion: this.manifest?.source?.spineVersion
                    || this._modelInfo?.skeleton?.spineVersion
                    || null,
                warnings: Object.freeze([...this.warnings]),
                animationCount: this.animationNames().length,
                skinCount: this._characterSkinCount || 0,
                cameraMode: 'fit-to-skeleton',
                referenceScale: this._referenceScale,
                sceneId: this._scene?.id || null,
                sceneVisible: this.skeleton?.visible !== false,
                sceneMirrored: Boolean(this._scene?.model?.mirrored),
            });
        }

        dispose() {
            if (this.disposed) return;
            this.playing = false;
            for (const { texture } of this._layerTextures.values()) texture.dispose();
            this._layerTextures.clear();
            try { this.assetManager?.dispose?.(); } catch (_) { /* best effort */ }
            try { this.renderer?.dispose?.(); } catch (_) { /* best effort */ }
            try { this.context?.dispose?.(); } catch (_) { /* best effort */ }
            this.assetManager = null;
            this.renderer = null;
            this.context = null;
            this.gl = null;
            this.atlas = null;
            this.skeletonData = null;
            this.skeleton = null;
            this.stateData = null;
            this.state = null;
            this._baseFit = null;
            this._referenceScale = 1;
            this._viewportBounds = null;
            this._scene = null;
            this.loaded = false;
            this.disposed = true;
        }
    }

    return Object.freeze({
        SpineRenderer,
        SpineRendererError,
        create: (options) => SpineRenderer.create(options),
        ensureRuntime,
        extractAtlasPageNames,
        normalizePath,
        constants: Object.freeze({
            DEFAULT_RUNTIME_VERSION,
            REQUIRED_RUNTIME_SYMBOLS,
        }),
    });
}));
