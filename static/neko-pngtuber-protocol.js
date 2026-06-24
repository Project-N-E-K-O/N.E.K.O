/**
 * NEKO PNGTuber protocol helpers.
 *
 * Keeps config normalization and load-plan creation separate from rendering so
 * runtime, model manager, and diagnostics can eventually share one contract.
 */
(function () {
    'use strict';

    const DEFAULT_PLACEHOLDER = '/static/icons/default_character_card.png';
    const IMAGE_KEYS = ['idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'];
    const SCALE_MIN = 0.1;
    const SCALE_MAX = 5;
    const NEKO_ADAPTER = 'neko_pngtuber_v2';
    const NEKO_METADATA_FORMAT = 'neko.pngtuber.v2';
    const NEKO_METADATA_FILENAME_PATTERN = /metadata\.neko-pngtuber\.v2\.json(?:[?#].*)?$/i;

    function clampNumber(value, min, max, fallback) {
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return fallback;
        return Math.max(min, Math.min(max, parsed));
    }

    function sanitizePath(value) {
        const raw = String(value || '').trim();
        if (!raw || raw === 'undefined' || raw === 'null') return '';
        return raw.replace(/\\/g, '/');
    }

    function normalizeAssetPath(value) {
        const path = sanitizePath(value);
        if (!path) return '';
        if (/^https?:\/\//i.test(path) || path.startsWith('/')) return path;
        return path;
    }

    function resolveSiblingAsset(baseUrl, value) {
        const path = sanitizePath(value);
        if (!path) return '';
        if (/^https?:\/\//i.test(path) || path.startsWith('/')) return path;
        const base = sanitizePath(baseUrl).split('/').slice(0, -1).join('/');
        return base ? `${base}/${path}` : path;
    }

    function isLayeredAdapter(adapter, metadataUrl) {
        const text = sanitizePath(adapter);
        return text === NEKO_ADAPTER && !!metadataUrl;
    }

    function inferAdapter(adapter, metadataUrl) {
        const text = sanitizePath(adapter);
        if (text) return text;
        if (!metadataUrl) return '';
        return NEKO_METADATA_FILENAME_PATTERN.test(metadataUrl) ? NEKO_ADAPTER : '';
    }

    function normalizeConfig(config, options = {}) {
        const source = config && typeof config === 'object' ? config : {};
        const normalized = Object.assign({}, source);
        IMAGE_KEYS.forEach((key) => {
            normalized[key] = normalizeAssetPath(source[key]);
        });
        normalized.idle_image = normalized.idle_image || options.placeholder || DEFAULT_PLACEHOLDER;
        normalized.talking_image = normalized.talking_image || normalized.idle_image;
        normalized.drag_image = normalized.drag_image || normalized.idle_image;
        normalized.click_image = normalized.click_image || normalized.talking_image;
        normalized.scale = clampNumber(source.scale, SCALE_MIN, SCALE_MAX, 1);
        const centerPreview = !!options.centerPreview && !source.preserve_model_manager_position;
        normalized.offset_x = centerPreview ? 0 : (Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0);
        normalized.offset_y = centerPreview ? 0 : (Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0);
        normalized.mirror = !!source.mirror;
        const metadataUrl = resolveSiblingAsset(
            normalized.idle_image,
            normalizeAssetPath(source.layered_metadata || source.metadata)
        );
        normalized.metadata = metadataUrl;
        normalized.layered_metadata = metadataUrl;
        normalized.adapter = inferAdapter(source.adapter, metadataUrl);
        normalized.source_format = sanitizePath(source.source_format || source.source_type);
        normalized.protocol = sanitizePath(source.protocol);
        return normalized;
    }

    function createLoadPlan(config, options = {}) {
        const normalized = normalizeConfig(config, options);
        const metadataUrl = normalized.metadata || normalized.layered_metadata || '';
        const layered = isLayeredAdapter(normalized.adapter, metadataUrl);
        return {
            protocol: 'neko.pngtuber.load_plan.v2',
            mode: layered ? 'layered' : 'image',
            renderer: layered ? 'layered_canvas' : 'image',
            adapter: normalized.adapter,
            metadataUrl,
            config: normalized,
            fallback: {
                idle: normalized.idle_image,
                talking: normalized.talking_image,
            },
            diagnostics: {
                hasMetadata: !!metadataUrl,
                sourceFormat: normalized.source_format || '',
            },
        };
    }

    function validateMetadata(metadata) {
        if (!metadata || typeof metadata !== 'object') {
            return { valid: false, reason: 'metadata is not an object' };
        }
        if (metadata.format !== NEKO_METADATA_FORMAT) {
            return { valid: false, reason: 'metadata format is not neko.pngtuber.v2' };
        }
        const runtime = String(metadata.runtime || '').trim();
        if (runtime !== 'neko_layered_canvas') {
            return { valid: false, reason: 'metadata runtime is not neko_layered_canvas' };
        }
        if (!Array.isArray(metadata.layers) || metadata.layers.length === 0) {
            return { valid: false, reason: 'metadata has no layers' };
        }
        if (!Number.isFinite(Number(metadata.state_count)) || Number(metadata.state_count) < 1) {
            return { valid: false, reason: 'metadata state_count is invalid' };
        }
        if (!metadata.emotions || typeof metadata.emotions !== 'object' || Array.isArray(metadata.emotions)) {
            return { valid: false, reason: 'metadata emotions is required' };
        }
        return { valid: true, reason: '' };
    }

    window.NekoPNGTuberProtocol = {
        IMAGE_KEYS,
        NEKO_ADAPTER,
        NEKO_METADATA_FORMAT,
        sanitizePath,
        resolveSiblingAsset,
        normalizeConfig,
        createLoadPlan,
        validateMetadata,
        isLayeredAdapter,
    };
})();
