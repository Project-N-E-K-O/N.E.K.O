(function () {
    'use strict';

    const DEFAULT_PLACEHOLDER = '/static/icons/default_character_card.png';
    const IMAGE_KEYS = ['idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'];
    const SCALE_MIN = 0.1;
    const SCALE_MAX = 5;
    const REMIX_FRAME_SPEED_MULTIPLIER = 4;

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

    function normalizeImagePath(value) {
        const path = sanitizePath(value);
        if (!path) return '';
        if (/^https?:\/\//i.test(path) || path.startsWith('/')) return path;
        return path;
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

    function loadImageElement(src) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
        });
    }

    function isModelManagerPage() {
        return window.location.pathname.includes('model_manager')
            || document.body?.classList.contains('model-manager-page')
            || document.getElementById('vrm-model-select') !== null;
    }

    function canInteractWithAvatar() {
        if (isModelManagerPage()) return true;
        return (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';
    }

    function normalizeConfig(config) {
        const source = config && typeof config === 'object' ? config : {};
        const normalized = Object.assign({}, source);
        IMAGE_KEYS.forEach((key) => {
            normalized[key] = normalizeImagePath(source[key]);
        });
        normalized.idle_image = normalized.idle_image || DEFAULT_PLACEHOLDER;
        normalized.talking_image = normalized.talking_image || normalized.idle_image;
        normalized.drag_image = normalized.drag_image || normalized.idle_image;
        normalized.click_image = normalized.click_image || normalized.talking_image;
        normalized.scale = clampNumber(source.scale, SCALE_MIN, SCALE_MAX, 1);
        const centerPreview = isModelManagerPage() && !source.preserve_model_manager_position;
        normalized.offset_x = centerPreview ? 0 : (Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0);
        normalized.offset_y = centerPreview ? 0 : (Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0);
        normalized.mirror = !!source.mirror;
        normalized.adapter = sanitizePath(source.adapter);
        const layeredMetadata = normalizeAssetPath(source.layered_metadata || source.metadata);
        normalized.layered_metadata = resolveSiblingAsset(normalized.idle_image, layeredMetadata);
        normalized.source_format = sanitizePath(source.source_format || source.source_type);
        return normalized;
    }

    class PNGTuberManager {
        constructor(containerId = 'pngtuber-container') {
            this.containerId = containerId;
            this.container = null;
            this.image = null;
            this.imageElement = null;
            this.canvasElement = null;
            this.config = normalizeConfig({});
            this.layeredMetadata = null;
            this.layeredImages = new Map();
            this.layeredBlinking = false;
            this.layeredBlinkTimer = null;
            this.layeredBlinkEndTimer = null;
            this.layeredStateIndex = 0;
            this.layeredStateReturnTimer = null;
            this.layeredAnimationFrame = null;
            this.layeredAnimationStart = 0;
            this._boundLayeredHotkey = (event) => this.handleLayeredHotkey(event);
            this._boundLayeredPlayEvent = (event) => this.handleLayeredPlayEvent(event);
            this._layeredHotkeysAttached = false;
            this._layeredPlayEventAttached = false;
            this.state = 'idle';
            this.returnIdleTimer = null;
            this.isSpeaking = false;
            this.speakingMouthTimer = null;
            this.speakingMouthOpen = false;
            this.speakingBounceFrame = null;
            this.speakingBounceStart = 0;
            this.speakingBounceDuration = 0;
            this.speakingBounceAmplitude = 0;
            this.speakingBounceSquish = 0;
            this.lastSpeakingBounceAt = 0;
            this.clickTimer = null;
            this._suppressNextClick = false;
            this._boundSpeechStart = () => this.setSpeaking(true);
            this._boundSpeechEnd = () => this.setSpeaking(false);
            this._listenersAttached = false;
            this._dragListenersAttached = false;
            this._dragState = null;
            this._saveInFlight = null;
            this._lastSavedPositionKey = '';
            this._saveTimer = null;
            this._touchZoomState = null;
            this.isLocked = false;
            this._lockIconElement = null;
            this._lockIconImages = null;
        }

        ensureContainer() {
            let container = document.getElementById(this.containerId);
            if (!container) {
                container = document.createElement('div');
                container.id = this.containerId;
                document.body.appendChild(container);
            }
            let image = container.querySelector('img.pngtuber-image');
            if (!image) {
                image = document.createElement('img');
                image.className = 'pngtuber-image';
                image.alt = 'PNGTuber avatar';
                image.draggable = false;
                container.appendChild(image);
            }
            let canvas = container.querySelector('canvas.pngtuber-layered-canvas');
            if (!canvas) {
                canvas = document.createElement('canvas');
                canvas.className = 'pngtuber-image pngtuber-layered-canvas';
                canvas.setAttribute('aria-label', 'PNGTuber layered avatar');
                container.appendChild(canvas);
            }
            this.container = container;
            this.imageElement = image;
            this.canvasElement = canvas;
            this.image = this.isLayeredActive() ? canvas : image;
            image.style.display = this.isLayeredActive() ? 'none' : '';
            canvas.style.display = this.isLayeredActive() ? '' : 'none';
            return container;
        }

        isLayeredConfigured() {
            return this.config.adapter === 'layered_canvas_v1' && !!this.config.layered_metadata;
        }

        isLayeredActive() {
            return this.isLayeredConfigured() && !!this.layeredMetadata && this.layeredImages.size > 0;
        }

        attachDragListeners() {
            this.ensureContainer();
            if (this._dragListenersAttached || !this.image) return;
            this._boundDragStart = (event) => this.startDrag(event);
            this._boundDragMove = (event) => this.moveDrag(event);
            this._boundDragEnd = (event) => this.endDrag(event);
            this._boundClick = (event) => this.handleClick(event);
            this._boundWheelZoom = (event) => this.handleWheelZoom(event);
            this._boundTouchStart = (event) => this.startTouchZoom(event);
            this._boundTouchMove = (event) => this.moveTouchZoom(event);
            this._boundTouchEnd = () => this.endTouchZoom();
            this.image.addEventListener('pointerdown', this._boundDragStart);
            this.image.addEventListener('click', this._boundClick);
            this.image.addEventListener('wheel', this._boundWheelZoom, { passive: false });
            this.image.addEventListener('touchstart', this._boundTouchStart, { passive: false });
            this.image.addEventListener('touchmove', this._boundTouchMove, { passive: false });
            this.image.addEventListener('touchend', this._boundTouchEnd, { passive: false });
            this.image.addEventListener('touchcancel', this._boundTouchEnd, { passive: false });
            window.addEventListener('pointermove', this._boundDragMove);
            window.addEventListener('pointerup', this._boundDragEnd);
            window.addEventListener('pointercancel', this._boundDragEnd);
            this._dragListenersAttached = true;
        }

        detachDragListeners() {
            if (!this._dragListenersAttached) return;
            if (this.image && this._boundDragStart) {
                this.image.removeEventListener('pointerdown', this._boundDragStart);
                this.image.removeEventListener('click', this._boundClick);
                this.image.removeEventListener('wheel', this._boundWheelZoom);
                this.image.removeEventListener('touchstart', this._boundTouchStart);
                this.image.removeEventListener('touchmove', this._boundTouchMove);
                this.image.removeEventListener('touchend', this._boundTouchEnd);
                this.image.removeEventListener('touchcancel', this._boundTouchEnd);
            }
            window.removeEventListener('pointermove', this._boundDragMove);
            window.removeEventListener('pointerup', this._boundDragEnd);
            window.removeEventListener('pointercancel', this._boundDragEnd);
            this._dragListenersAttached = false;
            this._dragState = null;
            this._touchZoomState = null;
            document.body.classList.remove('neko-model-dragging');
            if (this.image) this.image.classList.remove('is-dragging');
        }

        attachSpeechListeners() {
            if (this._listenersAttached) return;
            [
                'neko-assistant-speech-start',
                'neko-tts-playback-start',
                'neko-audio-playback-start',
                'assistant-speech-start'
            ].forEach((name) => window.addEventListener(name, this._boundSpeechStart));
            [
                'neko-assistant-speech-end',
                'neko-assistant-speech-cancel',
                'neko-tts-playback-end',
                'neko-audio-playback-end',
                'assistant-speech-end'
            ].forEach((name) => window.addEventListener(name, this._boundSpeechEnd));
            this._listenersAttached = true;
        }

        detachSpeechListeners() {
            if (!this._listenersAttached) return;
            [
                'neko-assistant-speech-start',
                'neko-tts-playback-start',
                'neko-audio-playback-start',
                'assistant-speech-start'
            ].forEach((name) => window.removeEventListener(name, this._boundSpeechStart));
            [
                'neko-assistant-speech-end',
                'neko-assistant-speech-cancel',
                'neko-tts-playback-end',
                'neko-audio-playback-end',
                'assistant-speech-end'
            ].forEach((name) => window.removeEventListener(name, this._boundSpeechEnd));
            this._listenersAttached = false;
        }

        preloadImages() {
            const seen = new Set();
            IMAGE_KEYS.forEach((key) => {
                const src = this.config[key];
                if (!src || seen.has(src)) return;
                seen.add(src);
                const img = new Image();
                img.src = src;
            });
        }

        clearLayeredTimers() {
            this.stopLayeredAnimationLoop();
            if (this.layeredBlinkTimer) {
                clearTimeout(this.layeredBlinkTimer);
                this.layeredBlinkTimer = null;
            }
            if (this.layeredBlinkEndTimer) {
                clearTimeout(this.layeredBlinkEndTimer);
                this.layeredBlinkEndTimer = null;
            }
            if (this.layeredStateReturnTimer) {
                clearTimeout(this.layeredStateReturnTimer);
                this.layeredStateReturnTimer = null;
            }
            this.layeredBlinking = false;
        }

        stopLayeredAnimationLoop() {
            if (this.layeredAnimationFrame) {
                cancelAnimationFrame(this.layeredAnimationFrame);
                this.layeredAnimationFrame = null;
            }
        }

        attachLayeredHotkeys() {
            if (this._layeredHotkeysAttached || !this.isLayeredActive()) return;
            const hotkeys = Array.isArray(this.layeredMetadata.hotkeys) ? this.layeredMetadata.hotkeys : [];
            if (hotkeys.length === 0) return;
            window.addEventListener('keydown', this._boundLayeredHotkey, true);
            this._layeredHotkeysAttached = true;
        }

        detachLayeredHotkeys() {
            if (!this._layeredHotkeysAttached) return;
            window.removeEventListener('keydown', this._boundLayeredHotkey, true);
            this._layeredHotkeysAttached = false;
        }

        attachLayeredPlayEvent() {
            if (this._layeredPlayEventAttached || !this.isLayeredActive()) return;
            window.addEventListener('pngtuber-play-animation', this._boundLayeredPlayEvent);
            this._layeredPlayEventAttached = true;
        }

        detachLayeredPlayEvent() {
            if (!this._layeredPlayEventAttached) return;
            window.removeEventListener('pngtuber-play-animation', this._boundLayeredPlayEvent);
            this._layeredPlayEventAttached = false;
        }

        handleLayeredPlayEvent(event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            const target = detail.animation ?? detail.state ?? detail.index ?? detail.key;
            this.playLayeredAnimation(target, {
                returnToDefaultAfterMs: detail.returnToDefaultAfterMs,
                source: 'event'
            });
        }

        getLayeredStateCount() {
            if (!this.layeredMetadata) return 1;
            return Math.max(1, Number(this.layeredMetadata.state_count) || 1);
        }

        resolveLayeredAnimationTarget(target) {
            const stateCount = this.getLayeredStateCount();
            if (typeof target === 'number' && Number.isFinite(target)) {
                const numeric = Math.trunc(target);
                return numeric >= 1 ? Math.min(stateCount - 1, numeric - 1) : 0;
            }
            const text = String(target ?? '').trim();
            if (!text) return 0;
            if (/^\d+$/.test(text)) {
                const numeric = Number(text);
                return numeric >= 1 ? Math.min(stateCount - 1, numeric - 1) : 0;
            }
            const normalized = text.toLowerCase();
            const hotkeys = Array.isArray(this.layeredMetadata?.hotkeys) ? this.layeredMetadata.hotkeys : [];
            const matched = hotkeys.find((hotkey) => {
                return String(hotkey.key || '').toLowerCase() === normalized
                    || String(hotkey.label || '').toLowerCase() === normalized
                    || String(hotkey.name || '').toLowerCase() === normalized;
            });
            if (matched) {
                return Math.max(0, Math.min(stateCount - 1, Number(matched.state_index) || 0));
            }
            return 0;
        }

        setLayeredStateIndex(index, options = {}) {
            if (!this.isLayeredActive()) return false;
            const stateCount = this.getLayeredStateCount();
            const nextIndex = Math.max(0, Math.min(stateCount - 1, Number(index) || 0));
            if (this.layeredStateReturnTimer) {
                clearTimeout(this.layeredStateReturnTimer);
                this.layeredStateReturnTimer = null;
            }
            this.layeredStateIndex = nextIndex;
            this.drawLayeredState();
            this.restartLayeredAnimationLoop();
            window.dispatchEvent(new CustomEvent('pngtuber-layered-state-changed', {
                detail: {
                    stateIndex: this.layeredStateIndex,
                    stateNumber: this.layeredStateIndex + 1,
                    source: options.source || 'api'
                }
            }));
            const returnDelay = Number(options.returnToDefaultAfterMs) || 0;
            if (returnDelay > 0 && this.layeredStateIndex !== 0) {
                this.layeredStateReturnTimer = setTimeout(() => {
                    this.layeredStateReturnTimer = null;
                    this.setLayeredStateIndex(0, { source: 'return' });
                }, Math.max(80, returnDelay));
            }
            return true;
        }

        playLayeredAnimation(target, options = {}) {
            if (!this.isLayeredActive()) return false;
            return this.setLayeredStateIndex(this.resolveLayeredAnimationTarget(target), {
                returnToDefaultAfterMs: options.returnToDefaultAfterMs,
                source: options.source || 'api'
            });
        }

        hotkeyMatchesEvent(hotkey, event) {
            const keycode = Number(hotkey.keycode || 0);
            if (keycode && event.keyCode !== keycode && event.which !== keycode) return false;
            if (!!hotkey.ctrl !== !!event.ctrlKey) return false;
            if (!!hotkey.shift !== !!event.shiftKey) return false;
            if (!!hotkey.alt !== !!event.altKey) return false;
            if (!!hotkey.meta !== !!event.metaKey) return false;
            return true;
        }

        handleLayeredHotkey(event) {
            if (!this.isLayeredActive()) return;
            const target = event.target;
            if (target && (
                target.tagName === 'INPUT'
                || target.tagName === 'TEXTAREA'
                || target.tagName === 'SELECT'
                || target.isContentEditable
            )) {
                return;
            }
            const hotkeys = Array.isArray(this.layeredMetadata.hotkeys) ? this.layeredMetadata.hotkeys : [];
            const matched = hotkeys.find((hotkey) => this.hotkeyMatchesEvent(hotkey, event));
            if (!matched) return;
            event.preventDefault();
            event.stopPropagation();
            this.setLayeredStateIndex(Number(matched.state_index) || 0, { source: 'hotkey' });
        }

        async setupLayeredAdapter() {
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            this.detachLayeredPlayEvent();
            this.layeredMetadata = null;
            this.layeredImages = new Map();
            this.layeredStateIndex = 0;
            if (!this.isLayeredConfigured()) return false;
            try {
                const response = await fetch(this.config.layered_metadata, { cache: 'no-cache' });
                if (!response.ok) throw new Error(`metadata ${response.status}`);
                const metadata = await response.json();
                const layers = Array.isArray(metadata.layers) ? metadata.layers : [];
                if (metadata.runtime !== 'layered_canvas' || layers.length === 0) {
                    throw new Error('metadata is not layered_canvas');
                }
                await Promise.all(layers.map(async (layer, index) => {
                    const src = resolveSiblingAsset(this.config.layered_metadata, layer.image);
                    if (!src) return;
                    const img = await loadImageElement(src);
                    this.layeredImages.set(index, img);
                    layer._imageIndex = index;
                }));
                if (this.layeredImages.size === 0) throw new Error('no layer images loaded');
                this.layeredMetadata = metadata;
                this.layeredStateIndex = 0;
                this.ensureContainer();
                const canvas = this.canvasElement;
                const canvasInfo = metadata.canvas || {};
                canvas.width = Math.max(1, Number(canvasInfo.width) || 1);
                canvas.height = Math.max(1, Number(canvasInfo.height) || 1);
                canvas.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
                this.startLayeredBlinkLoop();
                this.restartLayeredAnimationLoop();
                this.attachLayeredHotkeys();
                this.attachLayeredPlayEvent();
                return true;
            } catch (error) {
                console.warn('[PNGTuber] layered adapter disabled, falling back to image mode:', error);
                this.layeredMetadata = null;
                this.layeredImages = new Map();
                return false;
            }
        }

        hasBlinkLayers() {
            const layers = this.layeredMetadata && Array.isArray(this.layeredMetadata.layers)
                ? this.layeredMetadata.layers
                : [];
            return layers.some((layer) => {
                const state = layer.state || {};
                return Number(layer.showBlink || 0) !== 0 || !!state.should_blink;
            });
        }

        startLayeredBlinkLoop() {
            this.clearLayeredTimers();
            if (!this.isLayeredActive() || !this.hasBlinkLayers()) return;
            const blinkConfig = this.layeredMetadata.blink || {};
            if (blinkConfig.enabled === false) return;
            const minMs = Math.max(500, Number(blinkConfig.interval_min_ms) || 2800);
            const maxMs = Math.max(minMs, Number(blinkConfig.interval_max_ms) || 5200);
            const durationMs = Math.max(60, Number(blinkConfig.duration_ms) || 140);
            const schedule = () => {
                const delay = minMs + Math.random() * (maxMs - minMs);
                this.layeredBlinkTimer = setTimeout(() => {
                    this.layeredBlinking = true;
                    this.drawLayeredState();
                    this.layeredBlinkEndTimer = setTimeout(() => {
                        this.layeredBlinking = false;
                        this.drawLayeredState();
                        schedule();
                    }, durationMs);
                }, delay);
            };
            schedule();
        }

        shouldRenderLayer(layer, stateName) {
            if (layer.inactive_asset_ancestor) return false;
            const mode = stateName === 'talking' ? 'talking' : 'idle';
            const layerState = this.layerStateForCurrentIndex(layer);
            if (layerState.folder) return false;
            if (layerState.visible === false) return false;
            if (layerState.ancestor_visible === false) return false;
            if (layerState.ancestor_visible === undefined && layer.ancestor_visible === false) return false;
            const showTalk = Number(layer.showTalk || 0);
            if (showTalk !== 0) {
                if (mode === 'idle' && showTalk !== 1) return false;
                if (mode === 'talking' && showTalk !== 2) return false;
            }
            const showBlink = Number(layer.showBlink || 0);
            if (showBlink !== 0) {
                if (!this.layeredBlinking && showBlink === 2) return false;
                if (this.layeredBlinking && showBlink === 1) return false;
            }

            const shouldTalk = !!(layerState.effective_should_talk ?? layerState.should_talk);
            if (shouldTalk) {
                const openMouth = !!(layerState.effective_open_mouth ?? layerState.open_mouth);
                if (mode === 'idle' && openMouth) return false;
                if (mode === 'talking' && !openMouth) return false;
            }
            const shouldBlink = !!(layerState.effective_should_blink ?? layerState.should_blink);
            if (shouldBlink) {
                const openEyes = (layerState.effective_open_eyes ?? layerState.open_eyes) !== false;
                if (!this.layeredBlinking && !openEyes) return false;
                if (this.layeredBlinking && openEyes) return false;
            }
            return true;
        }

        layerStateForCurrentIndex(layer) {
            const states = Array.isArray(layer.states) ? layer.states : [];
            return states[this.layeredStateIndex] || layer.state || {};
        }

        stateHasMotion(layerState) {
            const hasXMotion = Math.abs(Number(layerState.xAmp) || 0) > 0.0001 && Math.abs(Number(layerState.xFrq) || 0) > 0.0001;
            const hasYMotion = Math.abs(Number(layerState.yAmp) || 0) > 0.0001 && Math.abs(Number(layerState.yFrq) || 0) > 0.0001;
            const hasWiggleMotion = Math.abs(Number(layerState.wiggle_amp) || 0) > 0.0001
                && Math.abs(Number(layerState.wiggle_freq || layerState.rot_frq) || 0) > 0.0001;
            return hasXMotion || hasYMotion || hasWiggleMotion || this.stateHasFrameAnimation(layerState);
        }

        stateFrameInfo(layer, layerState, img, timestamp = performance.now()) {
            const imageWidth = Number(layer.image_width || img.width) || img.width;
            const imageHeight = Number(layer.image_height || img.height) || img.height;
            const hframes = Math.max(1, Math.floor(Number(layerState.hframes) || Number(layer.hframes) || 1));
            const declaredFrames = Math.floor(Number(layerState.frames) || Number(layer.frames) || hframes);
            const frames = Math.max(1, declaredFrames);
            const rows = Math.max(1, Math.ceil(frames / hframes));
            const hasSheet = hframes > 1 || rows > 1;
            const computedFrameWidth = imageWidth / hframes;
            const computedFrameHeight = imageHeight / rows;
            const explicitFrameWidth = Number(layerState.frame_width) || Number(layer.frame_width);
            const explicitFrameHeight = Number(layerState.frame_height) || Number(layer.frame_height);
            const layerWidth = Number(layer.width) || 0;
            const layerHeight = Number(layer.height) || 0;
            const frameWidth = Math.max(1, Math.floor(
                explicitFrameWidth
                || (hasSheet ? computedFrameWidth : layerWidth)
                || computedFrameWidth
            ));
            const frameHeight = Math.max(1, Math.floor(
                explicitFrameHeight
                || (hasSheet ? computedFrameHeight : layerHeight)
                || computedFrameHeight
            ));
            const legacyFullSheetX = hasSheet && !explicitFrameWidth && layerWidth >= imageWidth;
            const legacyFullSheetY = hasSheet && !explicitFrameHeight && layerHeight >= imageHeight;
            let frame = Math.max(0, Math.floor(Number(layerState.frame) || 0));
            const speed = Math.max(0, Number(layerState.animation_speed) || Number(layer.animation_speed) || 0);
            const canAnimate = frames > 1
                && speed > 0
                && hasSheet
                && layerState.non_animated_sheet !== true;
            if (canAnimate) {
                const elapsedSeconds = Math.max(0, (timestamp - (this.layeredAnimationStart || timestamp)) / 1000);
                frame = Math.floor(elapsedSeconds * speed * REMIX_FRAME_SPEED_MULTIPLIER) % frames;
            }
            frame = Math.min(frames - 1, frame);
            return {
                sx: (frame % hframes) * frameWidth,
                sy: Math.floor(frame / hframes) * frameHeight,
                sw: frameWidth,
                sh: frameHeight,
                dw: frameWidth,
                dh: frameHeight,
                frame,
                frames,
                hframes,
                animated: canAnimate,
                legacyOffsetX: legacyFullSheetX ? (imageWidth - frameWidth) / 2 : 0,
                legacyOffsetY: legacyFullSheetY ? (imageHeight - frameHeight) / 2 : 0
            };
        }

        stateHasFrameAnimation(layerState) {
            const hframes = Math.max(1, Math.floor(Number(layerState.hframes) || 1));
            const frames = Math.max(1, Math.floor(Number(layerState.frames) || hframes));
            const speed = Math.max(0, Number(layerState.animation_speed) || 0);
            return frames > 1
                && speed > 0
                && (hframes > 1 || Math.ceil(frames / hframes) > 1)
                && layerState.non_animated_sheet !== true;
        }

        hasMotionLayersForCurrentState() {
            if (!this.isLayeredActive()) return false;
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            return layers.some((layer) => this.stateHasMotion(this.layerStateForCurrentIndex(layer)));
        }

        restartLayeredAnimationLoop() {
            this.stopLayeredAnimationLoop();
            if (!this.isLayeredActive() || !this.hasMotionLayersForCurrentState()) return;
            this.layeredAnimationStart = performance.now();
            const tick = (timestamp) => {
                if (!this.isLayeredActive()) {
                    this.stopLayeredAnimationLoop();
                    return;
                }
                this.drawLayeredState(this.state || 'idle', timestamp);
                this.layeredAnimationFrame = requestAnimationFrame(tick);
            };
            this.layeredAnimationFrame = requestAnimationFrame(tick);
        }

        motionValue(amplitude, frequency, timestamp, phase = 0) {
            const amp = Number(amplitude) || 0;
            const freq = Math.abs(Number(frequency) || 0);
            if (!amp || !freq) return 0;
            const elapsedSeconds = Math.max(0, (timestamp - (this.layeredAnimationStart || timestamp)) / 1000);
            const hz = Math.max(0.05, freq * 10);
            return Math.sin(elapsedSeconds * Math.PI * 2 * hz + phase) * amp;
        }

        drawLayeredState(stateName = this.state || 'idle', timestamp = performance.now()) {
            if (!this.isLayeredActive() || !this.canvasElement) return false;
            const canvas = this.canvasElement;
            const ctx = canvas.getContext('2d');
            if (!ctx) return false;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            layers
                .filter((layer) => this.shouldRenderLayer(layer, stateName))
                .sort((a, b) => {
                    const aState = this.layerStateForCurrentIndex(a);
                    const bState = this.layerStateForCurrentIndex(b);
                    return (Number(aState.z_index ?? a.zindex ?? 0) - Number(bState.z_index ?? b.zindex ?? 0))
                        || (Number(a.order || 0) - Number(b.order || 0));
                })
                .forEach((layer) => {
                    const img = this.layeredImages.get(layer._imageIndex);
                    if (!img) return;
                    const layerState = this.layerStateForCurrentIndex(layer);
                    const frame = this.stateFrameInfo(layer, layerState, img, timestamp);
                    const baseX = (Number(layerState.x ?? layer.x) || 0) + frame.legacyOffsetX;
                    const baseY = (Number(layerState.y ?? layer.y) || 0) + frame.legacyOffsetY;
                    const x = baseX + this.motionValue(layerState.xAmp, layerState.xFrq, timestamp, Number(layer.order || 0) * 0.17);
                    const y = baseY + this.motionValue(layerState.yAmp, layerState.yFrq, timestamp, Number(layer.order || 0) * 0.23);
                    const wiggleDegrees = this.motionValue(layerState.wiggle_amp, layerState.wiggle_freq || layerState.rot_frq, timestamp, Number(layer.order || 0) * 0.11);
                    const rotation = (Number(layerState.rotation) || 0) + wiggleDegrees * Math.PI / 180;
                    const rawScale = Array.isArray(layerState.scale) ? layerState.scale : [1, 1];
                    const baseScale = Array.isArray(layer.base_scale) ? layer.base_scale : [1, 1];
                    const baseScaleX = Number(baseScale[0]) || 1;
                    const baseScaleY = Number(baseScale[1]) || 1;
                    const relativeFlipX = !!layerState.flip_sprite_h !== !!layer.base_flip_h;
                    const relativeFlipY = !!layerState.flip_sprite_v !== !!layer.base_flip_v;
                    const scaleX = ((Number(rawScale[0]) || 1) / baseScaleX) * (relativeFlipX ? -1 : 1);
                    const scaleY = ((Number(rawScale[1]) || 1) / baseScaleY) * (relativeFlipY ? -1 : 1);
                    ctx.save();
                    ctx.translate(x + frame.dw / 2, y + frame.dh / 2);
                    if (rotation) ctx.rotate(rotation);
                    ctx.scale(scaleX, scaleY);
                    ctx.drawImage(
                        img,
                        frame.sx,
                        frame.sy,
                        frame.sw,
                        frame.sh,
                        -frame.dw / 2,
                        -frame.dh / 2,
                        frame.dw,
                        frame.dh
                    );
                    ctx.restore();
                });
            return true;
        }

        showTransientImage(src) {
            this.ensureContainer();
            if (this.isLayeredActive()) {
                const transientState = (src && (src === this.config.talking_image || src === this.config.click_image))
                    ? 'talking'
                    : (this.state || 'idle');
                this.drawLayeredState(transientState);
                this.applyTransform();
                this.updateLockIconPosition();
                return;
            }
            const nextSrc = src || this.config.drag_image || this.config.idle_image || DEFAULT_PLACEHOLDER;
            if (this.image && nextSrc && this.image.getAttribute('src') !== nextSrc) {
                this.image.src = nextSrc;
            }
            this.applyTransform();
            this.updateLockIconPosition();
        }

        showDragImage() {
            this.showTransientImage(this.config.drag_image || this.config.idle_image);
        }

        showClickImage() {
            this.showTransientImage(this.config.click_image || this.config.talking_image || this.config.idle_image);
        }

        restoreStateImage() {
            this.setState(this.state || 'idle');
        }

        applyTransform() {
            if (!this.image) return;
            const bounce = this.currentSpeakingBounceTransform();
            const scaleX = this.config.mirror ? -this.config.scale : this.config.scale;
            const finalScaleX = scaleX * bounce.scaleX;
            const finalScaleY = this.config.scale * bounce.scaleY;
            const modelManagerPage = isModelManagerPage();
            const pointerEvents = this.isLocked ? 'none' : 'auto';
            if (modelManagerPage) {
                Object.assign(this.image.style, {
                    position: 'absolute',
                    left: '50%',
                    top: '50%',
                    right: 'auto',
                    bottom: 'auto',
                    transformOrigin: 'center center',
                    pointerEvents
                });
            }
            if (!modelManagerPage) {
                this.image.style.pointerEvents = pointerEvents;
            }
            const anchorTranslate = modelManagerPage
                ? 'translate(-50%, -50%)'
                : 'translate(-100%, -100%)';
            this.image.style.transform = `${anchorTranslate} translate(${this.config.offset_x}px, ${this.config.offset_y + bounce.y}px) scale(${finalScaleX}, ${finalScaleY})`;
        }

        applyScale(nextScale) {
            const previousScale = Number(this.config.scale) || 1;
            this.config.scale = clampNumber(nextScale, SCALE_MIN, SCALE_MAX, previousScale);
            this.applyTransform();
            this.syncGlobalConfig();
            if (typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
            this.updateLockIconPosition();
        }

        syncGlobalConfig() {
            if (window.lanlan_config && typeof window.lanlan_config === 'object') {
                const modelType = (window.lanlan_config.model_type || '').toLowerCase();
                if (modelType === 'pngtuber') {
                    window.lanlan_config.pngtuber = Object.assign({}, this.config);
                }
            }
        }

        setLocked(locked, options = {}) {
            const { updateFloatingButtons = true } = options;
            this.isLocked = !!locked;
            if (this._lockIconImages) {
                const { locked: imgLocked, unlocked: imgUnlocked } = this._lockIconImages;
                if (imgLocked) imgLocked.style.opacity = this.isLocked ? '1' : '0';
                if (imgUnlocked) imgUnlocked.style.opacity = this.isLocked ? '0' : '1';
            }
            if (this.image) {
                this.image.style.pointerEvents = this.isLocked ? 'none' : 'auto';
                this.image.classList.toggle('is-locked', this.isLocked);
            }
            if (!this.isLocked && this.container) {
                this.container.classList.remove('locked-hover-fade');
            }
            if (updateFloatingButtons && this._floatingButtonsContainer) {
                this._floatingButtonsContainer.style.display = this.isLocked ? 'none' : 'flex';
            }
            if (typeof this.updateLockIconPosition === 'function') {
                this.updateLockIconPosition();
            }
            if (!this.isLocked && typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
        }

        startDrag(event) {
            if (!canInteractWithAvatar()) return;
            if (this.isLocked) return;
            if (event.button !== undefined && event.button !== 0) return;
            if (event.target && event.target.closest && event.target.closest('[id$="-floating-buttons"], [id$="-lock-icon"], [id$="-return-button-container"]')) return;
            event.preventDefault();
            event.stopPropagation();
            this._dragState = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                startOffsetX: Number(this.config.offset_x) || 0,
                startOffsetY: Number(this.config.offset_y) || 0,
                moved: false
            };
            if (this.image && typeof this.image.setPointerCapture === 'function') {
                try { this.image.setPointerCapture(event.pointerId); } catch (_) {}
            }
            document.body.classList.add('neko-model-dragging');
            if (this.image) this.image.classList.add('is-dragging');
        }

        moveDrag(event) {
            const state = this._dragState;
            if (!state || (state.pointerId !== undefined && event.pointerId !== state.pointerId)) return;
            event.preventDefault();
            const dx = event.clientX - state.startX;
            const dy = event.clientY - state.startY;
            if (Math.hypot(dx, dy) > 4 && !state.moved) {
                state.moved = true;
                this.showDragImage();
            }
            this.config.offset_x = Math.max(-5000, Math.min(5000, state.startOffsetX + dx));
            this.config.offset_y = Math.max(-5000, Math.min(5000, state.startOffsetY + dy));
            this.applyTransform();
            this.syncGlobalConfig();
            if (typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
            this.updateLockIconPosition();
        }

        async endDrag(event) {
            const state = this._dragState;
            if (!state || (state.pointerId !== undefined && event.pointerId !== state.pointerId)) return;
            this._dragState = null;
            if (this.image && typeof this.image.releasePointerCapture === 'function') {
                try { this.image.releasePointerCapture(event.pointerId); } catch (_) {}
            }
            document.body.classList.remove('neko-model-dragging');
            if (this.image) this.image.classList.remove('is-dragging');
            this.restoreStateImage();
            if (typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
            this.updateLockIconPosition();
            if (state.moved) {
                this._suppressNextClick = true;
                await this.saveCurrentConfig();
            }
        }

        handleClick(event) {
            if (!canInteractWithAvatar()) return;
            if (this.isLocked) return;
            if (this._suppressNextClick) {
                this._suppressNextClick = false;
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            if (event.target && event.target.closest && event.target.closest('[id$="-floating-buttons"], [id$="-lock-icon"], [id$="-return-button-container"]')) return;
            event.preventDefault();
            event.stopPropagation();
            if (this.clickTimer) clearTimeout(this.clickTimer);
            this.showClickImage();
            this.clickTimer = setTimeout(() => {
                this.clickTimer = null;
                this.restoreStateImage();
            }, 600);
        }

        handleWheelZoom(event) {
            if (!canInteractWithAvatar()) return;
            if (this.isLocked) return;
            if (this._dragState) return;
            event.preventDefault();
            event.stopPropagation();
            const absDelta = Math.abs(event.deltaY);
            const zoomStep = Math.min(absDelta / 1000, 0.08);
            const scaleFactor = 1 + zoomStep;
            const currentScale = Number(this.config.scale) || 1;
            const nextScale = event.deltaY < 0 ? currentScale * scaleFactor : currentScale / scaleFactor;
            this.applyScale(nextScale);
            this.scheduleSaveCurrentConfig();
        }

        getTouchDistance(touch1, touch2) {
            const dx = touch2.clientX - touch1.clientX;
            const dy = touch2.clientY - touch1.clientY;
            return Math.sqrt(dx * dx + dy * dy);
        }

        getTouchCenter(touch1, touch2) {
            return {
                x: (touch1.clientX + touch2.clientX) / 2,
                y: (touch1.clientY + touch2.clientY) / 2
            };
        }

        startTouchZoom(event) {
            if (!canInteractWithAvatar()) return;
            if (this.isLocked) return;
            if (!event.touches || event.touches.length !== 2) return;
            event.preventDefault();
            event.stopPropagation();
            const center = this.getTouchCenter(event.touches[0], event.touches[1]);
            this._dragState = null;
            this._touchZoomState = {
                initialDistance: this.getTouchDistance(event.touches[0], event.touches[1]),
                initialScale: Number(this.config.scale) || 1,
                startCenterX: center.x,
                startCenterY: center.y,
                startOffsetX: Number(this.config.offset_x) || 0,
                startOffsetY: Number(this.config.offset_y) || 0,
                changed: false
            };
            document.body.classList.add('neko-model-dragging');
            if (this.image) this.image.classList.add('is-dragging');
            this.showDragImage();
        }

        moveTouchZoom(event) {
            const state = this._touchZoomState;
            if (!state || !event.touches || event.touches.length !== 2 || state.initialDistance <= 0) return;
            event.preventDefault();
            event.stopPropagation();
            const currentDistance = this.getTouchDistance(event.touches[0], event.touches[1]);
            const center = this.getTouchCenter(event.touches[0], event.touches[1]);
            const scaleChange = currentDistance / state.initialDistance;
            const dx = center.x - state.startCenterX;
            const dy = center.y - state.startCenterY;
            state.changed = Math.abs(scaleChange - 1) > 0.01 || Math.hypot(dx, dy) > 4;
            this.config.offset_x = Math.max(-5000, Math.min(5000, state.startOffsetX + dx));
            this.config.offset_y = Math.max(-5000, Math.min(5000, state.startOffsetY + dy));
            this.applyScale(state.initialScale * scaleChange);
        }

        async endTouchZoom() {
            const state = this._touchZoomState;
            if (!state) return;
            this._touchZoomState = null;
            document.body.classList.remove('neko-model-dragging');
            if (this.image) this.image.classList.remove('is-dragging');
            this.restoreStateImage();
            if (typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
            this.updateLockIconPosition();
            if (state.changed) {
                await this.saveCurrentConfig();
            }
        }

        setupHTMLLockIcon() {
            if (isModelManagerPage()) return;
            const cfgType = (window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
            if (cfgType !== 'pngtuber') return;
            if (!document.getElementById('chat-container') || window.isViewerMode) {
                this.isLocked = false;
                if (this.image) this.image.style.pointerEvents = 'auto';
                return;
            }

            const existingLockIcon = document.getElementById('pngtuber-lock-icon');
            if (existingLockIcon) existingLockIcon.remove();

            const lockIcon = document.createElement('div');
            lockIcon.id = 'pngtuber-lock-icon';
            Object.assign(lockIcon.style, {
                position: 'fixed',
                zIndex: '99999',
                width: '32px',
                height: '32px',
                cursor: 'pointer',
                userSelect: 'none',
                pointerEvents: 'auto',
                transition: 'opacity 0.3s ease',
                display: 'none'
            });

            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : `?v=${Date.now()}`;
            const imgContainer = document.createElement('div');
            Object.assign(imgContainer.style, {
                position: 'relative',
                width: '32px',
                height: '32px'
            });

            const imgLocked = document.createElement('img');
            imgLocked.src = `/static/icons/locked_icon.png${iconVersion}`;
            imgLocked.alt = 'Locked';
            Object.assign(imgLocked.style, {
                position: 'absolute',
                width: '32px',
                height: '32px',
                objectFit: 'contain',
                pointerEvents: 'none',
                opacity: this.isLocked ? '1' : '0',
                transition: 'opacity 0.3s ease'
            });

            const imgUnlocked = document.createElement('img');
            imgUnlocked.src = `/static/icons/unlocked_icon.png${iconVersion}`;
            imgUnlocked.alt = 'Unlocked';
            Object.assign(imgUnlocked.style, {
                position: 'absolute',
                width: '32px',
                height: '32px',
                objectFit: 'contain',
                pointerEvents: 'none',
                opacity: this.isLocked ? '0' : '1',
                transition: 'opacity 0.3s ease'
            });

            imgContainer.appendChild(imgLocked);
            imgContainer.appendChild(imgUnlocked);
            lockIcon.appendChild(imgContainer);
            document.body.appendChild(lockIcon);

            this._lockIconElement = lockIcon;
            this._lockIconImages = { locked: imgLocked, unlocked: imgUnlocked };

            lockIcon.addEventListener('click', (event) => {
                event.stopPropagation();
                event.preventDefault();
                this.setLocked(!this.isLocked);
            });

            this.updateLockIconPosition();
        }

        updateLockIconPosition() {
            const lockIcon = this._lockIconElement || document.getElementById('pngtuber-lock-icon');
            if (!lockIcon) return;
            const image = this.image || (this.ensureContainer() && this.image);
            const rect = image ? image.getBoundingClientRect() : null;
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                if (!window.isInTutorial) lockIcon.style.display = 'none';
                return;
            }
            const lockGap = 28;
            const lockVerticalGap = 80;
            const targetX = rect.right * 0.7 + rect.left * 0.3 + lockGap;
            const targetY = rect.top * 0.3 + rect.bottom * 0.7 + lockVerticalGap;
            lockIcon.style.left = `${Math.max(0, Math.min(targetX, window.innerWidth - 40))}px`;
            lockIcon.style.top = `${Math.max(0, Math.min(targetY, window.innerHeight - 40))}px`;
            lockIcon.style.display = 'block';

            const lockRect = lockIcon.getBoundingClientRect();
            let isOverlapped = false;
            document.querySelectorAll('[id^="pngtuber-popup-"]').forEach((popup) => {
                if (popup.style.display === 'flex' && popup.style.opacity === '1') {
                    const popupRect = popup.getBoundingClientRect();
                    if (lockRect.right > popupRect.left && lockRect.left < popupRect.right &&
                        lockRect.bottom > popupRect.top && lockRect.top < popupRect.bottom) {
                        isOverlapped = true;
                    }
                }
            });
            if (!isOverlapped) {
                document.querySelectorAll('[data-neko-sidepanel]').forEach((panel) => {
                    if (panel.style.display !== 'none' && parseFloat(panel.style.opacity) > 0) {
                        const panelRect = panel.getBoundingClientRect();
                        if (lockRect.right > panelRect.left && lockRect.left < panelRect.right &&
                            lockRect.bottom > panelRect.top && lockRect.top < panelRect.bottom) {
                            isOverlapped = true;
                        }
                    }
                });
            }
            const shouldFade = this.container && this.container.classList.contains('locked-hover-fade');
            lockIcon.style.opacity = shouldFade ? '0.12' : (isOverlapped ? '0.3' : '');
        }

        async resolveCurrentLanlanName() {
            const direct = window.lanlan_config?.lanlan_name
                || window.lanlan_config?.name
                || window.current_lanlan_name
                || window.currentLanlanName
                || window.lanlanName;
            if (direct) return String(direct);
            try {
                const response = await fetch('/api/config');
                if (!response.ok) return '';
                const data = await response.json();
                return String(data.lanlan_name || data.current_lanlan || data.current_catgirl || data.name || '');
            } catch (_) {
                return '';
            }
        }

        async saveCurrentConfig() {
            if ((window.lanlan_config?.model_type || '').toLowerCase() !== 'pngtuber') {
                return false;
            }
            const saveKey = `${this.config.offset_x}:${this.config.offset_y}:${this.config.scale}:${this.config.mirror}`;
            if (saveKey === this._lastSavedPositionKey) return true;
            const runSave = async () => {
                const name = await this.resolveCurrentLanlanName();
                if (!name) {
                    console.warn('[PNGTuber] 无法解析当前角色名，跳过位置保存');
                    return false;
                }
                const payload = {
                    model_type: 'pngtuber',
                    pngtuber: Object.assign({}, this.config)
                };
                const response = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(name)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await response.json().catch(() => ({}));
                if (!response.ok || !result.success) {
                    console.warn('[PNGTuber] 保存位置失败:', result.error || response.statusText);
                    return false;
                }
                this._lastSavedPositionKey = saveKey;
                return true;
            };
            this._saveInFlight = (this._saveInFlight || Promise.resolve()).then(runSave, runSave);
            return this._saveInFlight;
        }

        scheduleSaveCurrentConfig(delayMs = 250) {
            if (this._saveTimer) clearTimeout(this._saveTimer);
            this._saveTimer = setTimeout(() => {
                this._saveTimer = null;
                this.saveCurrentConfig();
            }, delayMs);
        }

        async load(config) {
            this.detachDragListeners();
            this.config = normalizeConfig(config || {});
            await this.setupLayeredAdapter();
            this.ensureContainer();
            this.preloadImages();
            this.attachSpeechListeners();
            this.attachDragListeners();
            this.setState('idle');
            this.applyTransform();
            this.syncGlobalConfig();
            if (typeof this.setupFloatingButtons === 'function') {
                this.setupFloatingButtons();
            }
            this.setupHTMLLockIcon();
            return true;
        }

        stateToSrc(state) {
            if (state === 'talking') return this.config.talking_image || this.config.idle_image || DEFAULT_PLACEHOLDER;
            const emotionKey = `${state}_image`;
            return this.config[emotionKey] || this.config.idle_image || DEFAULT_PLACEHOLDER;
        }

        setState(state) {
            this.state = state || 'idle';
            this.ensureContainer();
            if (this.isLayeredActive()) {
                this.drawLayeredState(this.state);
                this.restartLayeredAnimationLoop();
                this.applyTransform();
                this.updateLockIconPosition();
                return;
            }
            const nextSrc = this.stateToSrc(this.state);
            if (this.image && this.image.getAttribute('src') !== nextSrc) {
                this.image.src = nextSrc;
            }
            this.applyTransform();
            this.updateLockIconPosition();
        }

        currentRemixStateSettings() {
            const settings = this.layeredMetadata && this.layeredMetadata.settings;
            const states = settings && Array.isArray(settings.states) ? settings.states : [];
            return states[this.layeredStateIndex] || states[0] || {};
        }

        speakingBounceConfig() {
            const settings = this.layeredMetadata && this.layeredMetadata.settings;
            const stateSettings = this.currentRemixStateSettings();
            const mouthAnimation = String(stateSettings.current_mo_anim || '').toLowerCase();
            if (!mouthAnimation.includes('bounce')) return null;
            const gravity = Math.max(100, Number(settings?.bounceGravity) || 575);
            const slider = Math.max(0, Number(settings?.bounceSlider) || 250);
            const squishAmount = Number(stateSettings.squish_amount) || 1;
            return {
                amplitude: Math.max(4, Math.min(22, slider / 18)),
                duration: Math.max(180, Math.min(520, 90000 / gravity + 170)),
                squish: Math.max(0, Math.min(0.08, Math.abs(squishAmount - 1) * 1.8 || 0.025))
            };
        }

        currentSpeakingBounceTransform(now = performance.now()) {
            if (!this.speakingBounceStart || !this.speakingBounceDuration) {
                return { y: 0, scaleX: 1, scaleY: 1 };
            }
            const progress = (now - this.speakingBounceStart) / this.speakingBounceDuration;
            if (progress >= 1) {
                return { y: 0, scaleX: 1, scaleY: 1 };
            }
            const clamped = Math.max(0, progress);
            const peakAt = 0.28;
            const lift = clamped < peakAt
                ? Math.sin((clamped / peakAt) * Math.PI / 2)
                : (1 + Math.cos(((clamped - peakAt) / (1 - peakAt)) * Math.PI)) / 2;
            const landing = clamped > 0.68 ? Math.sin(Math.PI * Math.min(1, (clamped - 0.68) / 0.32)) : 0;
            return {
                y: -this.speakingBounceAmplitude * lift,
                scaleX: 1 + this.speakingBounceSquish * landing,
                scaleY: 1 - this.speakingBounceSquish * landing
            };
        }

        stopSpeakingBounceAnimation() {
            if (this.speakingBounceFrame) {
                cancelAnimationFrame(this.speakingBounceFrame);
                this.speakingBounceFrame = null;
            }
            this.speakingBounceStart = 0;
            this.speakingBounceDuration = 0;
            this.speakingBounceAmplitude = 0;
            this.speakingBounceSquish = 0;
            this.applyTransform();
        }

        startSpeakingBounceAnimation() {
            const config = this.speakingBounceConfig();
            if (!config) return;
            const now = performance.now();
            if (now - this.lastSpeakingBounceAt < 220) return;
            this.lastSpeakingBounceAt = now;
            this.speakingBounceStart = now;
            this.speakingBounceDuration = config.duration;
            this.speakingBounceAmplitude = config.amplitude;
            this.speakingBounceSquish = config.squish;
            if (this.speakingBounceFrame) {
                cancelAnimationFrame(this.speakingBounceFrame);
                this.speakingBounceFrame = null;
            }
            const tick = () => {
                const progress = (performance.now() - this.speakingBounceStart) / this.speakingBounceDuration;
                if (progress >= 1 || !this.container || this.container.style.display === 'none') {
                    this.speakingBounceFrame = null;
                    this.speakingBounceStart = 0;
                    this.applyTransform();
                    return;
                }
                this.applyTransform();
                this.updateLockIconPosition();
                if (typeof this.updateFloatingButtonsPosition === 'function') {
                    this.updateFloatingButtonsPosition();
                }
                this.speakingBounceFrame = requestAnimationFrame(tick);
            };
            this.speakingBounceFrame = requestAnimationFrame(tick);
        }

        scheduleSpeakingMouthFrame() {
            if (!this.isSpeaking) return;
            const nextDelay = this.speakingMouthOpen
                ? 80 + Math.random() * 90
                : 55 + Math.random() * 95;
            this.speakingMouthTimer = setTimeout(() => {
                this.speakingMouthTimer = null;
                if (!this.isSpeaking) return;
                this.speakingMouthOpen = !this.speakingMouthOpen;
                if (this.speakingMouthOpen) {
                    this.startSpeakingBounceAnimation();
                }
                this.setState(this.speakingMouthOpen ? 'talking' : 'idle');
                this.scheduleSpeakingMouthFrame();
            }, nextDelay);
        }

        startSpeakingMouthAnimation() {
            this.isSpeaking = true;
            if (this.speakingMouthTimer) return;
            this.speakingMouthOpen = true;
            this.startSpeakingBounceAnimation();
            this.setState('talking');
            this.scheduleSpeakingMouthFrame();
        }

        stopSpeakingMouthAnimation() {
            this.isSpeaking = false;
            this.speakingMouthOpen = false;
            if (this.speakingMouthTimer) {
                clearTimeout(this.speakingMouthTimer);
                this.speakingMouthTimer = null;
            }
            this.stopSpeakingBounceAnimation();
        }

        renderedLayerCountForState(stateName) {
            if (!this.isLayeredActive()) return 0;
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            return layers.filter((layer) => this.shouldRenderLayer(layer, stateName)).length;
        }

        renderedLayerDebugInfo(stateName) {
            if (!this.isLayeredActive()) return [];
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            return layers
                .filter((layer) => this.shouldRenderLayer(layer, stateName))
                .sort((a, b) => {
                    const aState = this.layerStateForCurrentIndex(a);
                    const bState = this.layerStateForCurrentIndex(b);
                    return (Number(aState.z_index ?? a.zindex ?? 0) - Number(bState.z_index ?? b.zindex ?? 0))
                        || (Number(a.order || 0) - Number(b.order || 0));
                })
                .map((layer) => {
                    const layerState = this.layerStateForCurrentIndex(layer);
                    const img = this.layeredImages.get(layer._imageIndex);
                    const frame = img ? this.stateFrameInfo(layer, layerState, img) : null;
                    return {
                        name: layer.name || '',
                        order: Number(layer.order || 0),
                        sprite_id: layer.sprite_id ?? null,
                        parent_id: layer.parent_id ?? null,
                        x: Number(layerState.x ?? layer.x ?? 0),
                        y: Number(layerState.y ?? layer.y ?? 0),
                        width: Number(layerState.frame_width ?? layer.width ?? 0),
                        height: Number(layerState.frame_height ?? layer.height ?? 0),
                        image_width: Number(layer.image_width ?? 0),
                        image_height: Number(layer.image_height ?? 0),
                        frame: frame ? frame.frame : Number(layerState.frame || 0),
                        frames: frame ? frame.frames : Number(layerState.frames || layerState.hframes || 1),
                        hframes: frame ? frame.hframes : Number(layerState.hframes || 1),
                        frame_animated: !!(frame && frame.animated),
                        visible: layerState.visible !== false,
                        ancestor_visible: layerState.ancestor_visible ?? layer.ancestor_visible ?? true,
                        should_talk: !!(layerState.effective_should_talk ?? layerState.should_talk),
                        open_mouth: !!(layerState.effective_open_mouth ?? layerState.open_mouth),
                        should_blink: !!(layerState.effective_should_blink ?? layerState.should_blink),
                        open_eyes: (layerState.effective_open_eyes ?? layerState.open_eyes) !== false
                    };
                });
        }

        getDebugState() {
            const container = this.container || document.getElementById(this.containerId);
            const image = this.image || this.imageElement || this.canvasElement;
            const stateSettings = this.currentRemixStateSettings();
            const now = performance.now();
            const bounceProgress = this.speakingBounceStart && this.speakingBounceDuration
                ? Math.max(0, Math.min(1, (now - this.speakingBounceStart) / this.speakingBounceDuration))
                : 0;
            const layers = this.layeredMetadata && Array.isArray(this.layeredMetadata.layers)
                ? this.layeredMetadata.layers
                : [];
            const imageRect = image && typeof image.getBoundingClientRect === 'function'
                ? image.getBoundingClientRect()
                : null;
            return {
                active: !!(container && container.style.display !== 'none' && !container.classList.contains('hidden')),
                modelType: (window.lanlan_config?.model_type || '').toLowerCase() || null,
                state: this.state,
                isSpeaking: !!this.isSpeaking,
                speakingMouthOpen: !!this.speakingMouthOpen,
                layered: this.isLayeredActive(),
                layeredConfigured: this.isLayeredConfigured(),
                layeredStateIndex: this.layeredStateIndex,
                layerCount: layers.length,
                renderedIdleLayerCount: this.renderedLayerCountForState('idle'),
                renderedTalkingLayerCount: this.renderedLayerCountForState('talking'),
                renderedLayers: this.renderedLayerDebugInfo(this.state || 'idle'),
                currentMoAnim: stateSettings.current_mo_anim || null,
                currentMcAnim: stateSettings.current_mc_anim || null,
                bounceActive: !!(this.speakingBounceFrame || (bounceProgress > 0 && bounceProgress < 1)),
                bounceProgress,
                timers: {
                    mouthTimer: !!this.speakingMouthTimer,
                    bounceFrame: !!this.speakingBounceFrame,
                    blinkTimer: !!this.layeredBlinkTimer,
                    blinkEndTimer: !!this.layeredBlinkEndTimer,
                    returnIdleTimer: !!this.returnIdleTimer,
                    layeredAnimationFrame: !!this.layeredAnimationFrame
                },
                container: {
                    id: this.containerId,
                    exists: !!container,
                    display: container ? container.style.display || '' : '',
                    visibility: container ? container.style.visibility || '' : '',
                    hiddenClass: !!(container && container.classList.contains('hidden'))
                },
                image: {
                    tag: image ? image.tagName : null,
                    src: image && image.getAttribute ? image.getAttribute('src') : null,
                    width: imageRect ? Math.round(imageRect.width) : 0,
                    height: imageRect ? Math.round(imageRect.height) : 0,
                    transform: image && image.style ? image.style.transform || '' : ''
                }
            };
        }

        setSpeaking(isSpeaking) {
            if (this.returnIdleTimer) {
                clearTimeout(this.returnIdleTimer);
                this.returnIdleTimer = null;
            }
            if (this.clickTimer) {
                clearTimeout(this.clickTimer);
                this.clickTimer = null;
            }
            if (isSpeaking) {
                this.startSpeakingMouthAnimation();
                return;
            }
            this.stopSpeakingMouthAnimation();
            this.returnIdleTimer = setTimeout(() => {
                this.returnIdleTimer = null;
                this.setState('idle');
            }, 160);
        }

        show() {
            this.ensureContainer();
            this.container.classList.remove('hidden');
            this.container.style.display = 'block';
            this.container.style.visibility = 'visible';
            if (this.isLayeredActive()) {
                this.drawLayeredState();
                if (!this.layeredBlinkTimer && !this.layeredBlinkEndTimer) {
                    this.startLayeredBlinkLoop();
                }
                this.restartLayeredAnimationLoop();
                this.attachLayeredHotkeys();
            }
        }

        hide() {
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            if (this.returnIdleTimer) {
                clearTimeout(this.returnIdleTimer);
                this.returnIdleTimer = null;
            }
            if (this.clickTimer) {
                clearTimeout(this.clickTimer);
                this.clickTimer = null;
            }
            this.stopSpeakingMouthAnimation();
            const container = this.container || document.getElementById(this.containerId);
            if (container) {
                container.style.display = 'none';
                container.classList.add('hidden');
            }
        }

        dispose() {
            this.detachSpeechListeners();
            this.detachDragListeners();
            if (this._saveTimer) {
                clearTimeout(this._saveTimer);
                this._saveTimer = null;
            }
            if (this.returnIdleTimer) {
                clearTimeout(this.returnIdleTimer);
                this.returnIdleTimer = null;
            }
            if (this.clickTimer) {
                clearTimeout(this.clickTimer);
                this.clickTimer = null;
            }
            if (typeof this.cleanupFloatingButtons === 'function') {
                this.cleanupFloatingButtons();
            }
            this._lockIconElement = null;
            this._lockIconImages = null;
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            this.layeredMetadata = null;
            this.layeredImages = new Map();
            if (this.image) {
                if (this.image.removeAttribute) this.image.removeAttribute('src');
            }
            this.hide();
        }
    }

    function applyPNGTuberAvatarUiMixins() {
        if (PNGTuberManager.prototype._pngtuberAvatarUiApplied) return;
        if (typeof AvatarPopupMixin !== 'undefined') {
            AvatarPopupMixin.apply(PNGTuberManager.prototype, 'pngtuber', {
                animationDurationMs: typeof AVATAR_POPUP_ANIMATION_DURATION_MS !== 'undefined'
                    ? AVATAR_POPUP_ANIMATION_DURATION_MS
                    : 200,
                characterMenuItems: [
                    { id: 'general', label: '通用设置', labelKey: 'settings.menu.general', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', url: '/character_card_manager' },
                    { id: 'pngtuber-manage', label: '模型管理', labelKey: 'settings.menu.modelSettings', icon: '/static/icons/character_icon.png', action: 'navigate', urlBase: '/model_manager' },
                    { id: 'voice-clone', label: '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' }
                ],
                onMouseTrackingToggle: function(enabled) {
                    window.mouseTrackingEnabled = enabled;
                },
                getMouseTrackingState: function() {
                    return window.mouseTrackingEnabled !== false;
                }
            });
        }
        if (typeof AvatarButtonMixin !== 'undefined') {
            AvatarButtonMixin.apply(PNGTuberManager.prototype, 'pngtuber', {
                containerElementId: 'pngtuber-floating-buttons',
                returnContainerId: 'pngtuber-return-button-container',
                returnBtnId: 'pngtuber-btn-return',
                lockIconId: 'pngtuber-lock-icon',
                popupPrefix: 'pngtuber',
                buttonClassPrefix: 'pngtuber-floating-btn',
                triggerBtnClass: 'pngtuber-trigger-btn',
                triggerIconClass: 'pngtuber-trigger-icon',
                returnBtnClass: 'pngtuber-return-btn',
                returnBreathingStyleId: 'pngtuber-return-button-breathing-styles'
            });
        }
        PNGTuberManager.prototype._pngtuberAvatarUiApplied = true;
    }

    function installPNGTuberFloatingButtons() {
        applyPNGTuberAvatarUiMixins();
        if (typeof PNGTuberManager.prototype.setupFloatingButtonsBase !== 'function') return;

        PNGTuberManager.prototype.setupFloatingButtons = function() {
            if (isModelManagerPage()) return;
            const cfgType = (window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
            if (cfgType && cfgType !== 'pngtuber') return;

            const buttonsContainer = this.setupFloatingButtonsBase();
            const prefix = this._avatarPrefix || 'pngtuber';
            this._floatingButtons = this._floatingButtons || {};
            this._buttonConfigs = this.getDefaultButtonConfigs();

            this.updateFloatingButtonsPosition = () => {
                if (this._isInReturnState) {
                    buttonsContainer.style.display = 'none';
                    return;
                }
                if (this.isLocked) {
                    buttonsContainer.style.display = 'none';
                    this.updateLockIconPosition();
                    return;
                }
                const isMobile = window.isMobileWidth && window.isMobileWidth();
                if (isMobile) {
                    buttonsContainer.style.flexDirection = 'column';
                    buttonsContainer.style.bottom = '116px';
                    buttonsContainer.style.right = '16px';
                    buttonsContainer.style.left = '';
                    buttonsContainer.style.top = '';
                    buttonsContainer.style.display = 'flex';
                    buttonsContainer.style.visibility = 'visible';
                    buttonsContainer.style.opacity = '1';
                    return;
                }

                const image = this.image || (this.ensureContainer() && this.image);
                const rect = image ? image.getBoundingClientRect() : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    buttonsContainer.style.display = 'none';
                    return;
                }
                const visibleButtons = Array.from(buttonsContainer.children).filter((child) => {
                    const style = window.getComputedStyle(child);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                });
                const buttonWidth = 82;
                const buttonHeight = Math.max(48, visibleButtons.length * 48 + Math.max(0, visibleButtons.length - 1) * 12);
                const targetX = rect.right * 0.8 + rect.left * 0.2;
                const maxX = window.innerWidth - buttonWidth - 12;
                const left = Math.max(12, Math.min(targetX, maxX));
                let top = rect.top + (rect.height - buttonHeight) / 2;
                top = Math.max(12, Math.min(window.innerHeight - buttonHeight - 12, top));
                buttonsContainer.style.flexDirection = 'column';
                buttonsContainer.style.left = `${left}px`;
                buttonsContainer.style.top = `${top}px`;
                buttonsContainer.style.right = '';
                buttonsContainer.style.bottom = '';
                buttonsContainer.style.display = 'flex';
                buttonsContainer.style.visibility = 'visible';
                buttonsContainer.style.opacity = '1';
            };
            const applyResponsiveFloatingLayout = this.updateFloatingButtonsPosition;

            const buttonConfigs = this._buttonConfigs;
            buttonConfigs.forEach((config) => {
                if (window.isMobileWidth && window.isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) return;
                const { btnWrapper, btn, imgOff, imgOn } = this.createButtonElement(config, buttonsContainer);

                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    if (config.id === 'screen') {
                        const isRecording = window.isRecording || false;
                        const wantToActivate = btn.dataset.active !== 'true';
                        if (wantToActivate && !isRecording) {
                            if (typeof window.showStatusToast === 'function') {
                                window.showStatusToast(window.t ? window.t('app.screenShareRequiresVoice') : '屏幕分享仅用于音视频通话', 3000);
                            }
                            return;
                        }
                    }
                    if (config.popupToggle) return;
                    const targetActive = btn.dataset.active !== 'true';
                    if (config.id === 'mic' || config.id === 'screen') {
                        window.dispatchEvent(new CustomEvent(`live2d-${config.id}-toggle`, { detail: { active: targetActive } }));
                        this.setButtonActive(config.id, targetActive);
                    } else if (config.id === 'goodbye') {
                        this._isInReturnState = true;
                        window.dispatchEvent(new CustomEvent('live2d-goodbye-click'));
                    }
                });

                btnWrapper.appendChild(btn);
                if (config.id === 'mic' && config.hasPopup && config.separatePopupTrigger && !(window.isMobileWidth && window.isMobileWidth())) {
                    this.createMicMuteButton(btnWrapper);
                }

                let triggerBtn = null;
                let triggerImg = null;
                if (config.hasPopup && config.separatePopupTrigger) {
                    if (window.isMobileWidth && window.isMobileWidth() && config.id === 'mic') {
                        buttonsContainer.appendChild(btnWrapper);
                        this._floatingButtons[config.id] = { button: btn, imgOff, imgOn, triggerButton: null, triggerImg: null };
                        return;
                    }
                    const popup = this.createPopup(config.id);
                    triggerBtn = document.createElement('button');
                    triggerBtn.type = 'button';
                    triggerBtn.className = 'pngtuber-trigger-btn';
                    triggerBtn.setAttribute('aria-label', 'Open popup');
                    const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : '?v=1.0.0';
                    triggerImg = document.createElement('img');
                    triggerImg.src = '/static/icons/play_trigger_icon.png' + iconVersion;
                    triggerImg.alt = '';
                    triggerImg.className = `pngtuber-trigger-icon-${config.id}`;
                    Object.assign(triggerImg.style, {
                        width: '22px', height: '22px', objectFit: 'contain', pointerEvents: 'none',
                        imageRendering: 'crisp-edges', transition: 'transform 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)'
                    });
                    Object.assign(triggerBtn.style, {
                        width: '24px', height: '24px', borderRadius: '50%',
                        background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
                        backdropFilter: 'saturate(180%) blur(20px)',
                        border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                        userSelect: 'none', boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                        transition: 'all 0.1s ease', pointerEvents: 'auto', marginLeft: '-10px'
                    });
                    triggerBtn.appendChild(triggerImg);
                    triggerBtn.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                        this.showPopup(config.id, popup);
                        if (isVisible) return;
                        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        if (config.id === 'mic' && typeof window.renderFloatingMicList === 'function') {
                            await window.renderFloatingMicList(popup);
                        } else if (config.id === 'screen') {
                            await this.renderScreenSourceList(popup);
                        }
                    });
                    const triggerWrapper = document.createElement('div');
                    triggerWrapper.style.position = 'relative';
                    triggerWrapper.appendChild(triggerBtn);
                    triggerWrapper.appendChild(popup);
                    btnWrapper.appendChild(triggerWrapper);
                } else if (config.popupToggle) {
                    const popup = this.createPopup(config.id);
                    btnWrapper.appendChild(popup);
                    btn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        if (config.exclusive) this.closePopupById(config.exclusive);
                        this.showPopup(config.id, popup);
                    });
                }

                buttonsContainer.appendChild(btnWrapper);
                this._floatingButtons[config.id] = { button: btn, imgOff, imgOn, triggerButton: triggerBtn, triggerImg };
            });

            const returnHandler = () => {
                this._isInReturnState = false;
                if (this._returnButtonContainer) this._returnButtonContainer.style.display = 'none';
                applyResponsiveFloatingLayout();
            };
            this._uiWindowHandlers.push({ event: 'pngtuber-return-click', handler: returnHandler, target: window });
            this._uiWindowHandlers.push({ event: 'live2d-return-click', handler: returnHandler, target: window });
            window.addEventListener('pngtuber-return-click', returnHandler);
            window.addEventListener('live2d-return-click', returnHandler);
            this.createReturnButton();

            const scheduleLayout = () => requestAnimationFrame(() => {
                applyResponsiveFloatingLayout();
                this.updateLockIconPosition();
            });
            this._uiWindowHandlers.push({ event: 'resize', handler: scheduleLayout, target: window });
            this._uiWindowHandlers.push({ event: 'orientationchange', handler: scheduleLayout, target: window });
            window.addEventListener('resize', scheduleLayout);
            window.addEventListener('orientationchange', scheduleLayout);
            if (this.image) {
                this.image.addEventListener('load', scheduleLayout);
                this._uiWindowHandlers.push({ event: 'load', handler: scheduleLayout, target: this.image });
            }

            setTimeout(applyResponsiveFloatingLayout, 0);
            setTimeout(applyResponsiveFloatingLayout, 120);
            this._syncButtonStatesWithGlobalState();

            if (this._outsideClickHandler) document.removeEventListener('click', this._outsideClickHandler);
            this._outsideClickHandler = (e) => {
                const path = e.composedPath ? e.composedPath() : (e.path || []);
                if (path.includes(buttonsContainer)) return;
                if (path.some(n => n && n.id && n.id.startsWith('pngtuber-popup-'))) return;
                if (path.some(n => n && typeof n.hasAttribute === 'function' && n.hasAttribute('data-neko-sidepanel'))) return;
                this.closeAllPopups();
            };
            document.addEventListener('click', this._outsideClickHandler);
            this._uiWindowHandlers.push({ event: 'click', handler: this._outsideClickHandler, target: document });

            window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));
            window.dispatchEvent(new CustomEvent('pngtuber-floating-buttons-ready'));
        };
    }

    installPNGTuberFloatingButtons();

    async function hideOtherAvatarRuntimesForPNGTuber() {
        if (document.body?.classList.contains('model-manager-page')
            && window._modelManagerCurrentAvatarType
            && window._modelManagerCurrentAvatarType !== 'pngtuber') {
            return;
        }

        if (window.live2dManager) {
            try {
                window.live2dManager._activeLoadToken = (window.live2dManager._activeLoadToken || 0) + 1;
                window.live2dManager._isLoadingModel = false;
                if (typeof window.live2dManager.removeModel === 'function') {
                    await window.live2dManager.removeModel({ skipCloseWindows: true });
                } else {
                    window.live2dManager.currentModel = null;
                }
            } catch (error) {
                console.warn('[PNGTuber] 清理 Live2D runtime 失败:', error);
            }
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.style.display = 'none';
            live2dContainer.classList.add('hidden');
        }
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.visibility = 'hidden';
            live2dCanvas.style.pointerEvents = 'none';
        }
        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) {
            vrmContainer.style.display = 'none';
            vrmContainer.classList.add('hidden');
        }
        const mmdContainer = document.getElementById('mmd-container');
        if (mmdContainer) {
            mmdContainer.style.display = 'none';
            mmdContainer.classList.add('hidden');
        }
        document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container, #vrm-floating-buttons, #vrm-lock-icon, #vrm-return-button-container, #mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container')
            .forEach((el) => {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    async function loadPNGTuberAvatar(config) {
        await hideOtherAvatarRuntimesForPNGTuber();
        if (!window.pngtuberManager) {
            window.pngtuberManager = new PNGTuberManager();
        }
        await window.pngtuberManager.load(config || {});
        if (document.body?.classList.contains('model-manager-page')
            && window._modelManagerCurrentAvatarType
            && window._modelManagerCurrentAvatarType !== 'pngtuber') {
            window.pngtuberManager.hide();
            return window.pngtuberManager;
        }
        await hideOtherAvatarRuntimesForPNGTuber();
        window.pngtuberManager.show();
        await hideOtherAvatarRuntimesForPNGTuber();
        window.dispatchEvent(new CustomEvent('pngtuber-model-loaded'));
        return window.pngtuberManager;
    }

    function playPNGTuberAnimation(target, options = {}) {
        if (!window.pngtuberManager || typeof window.pngtuberManager.playLayeredAnimation !== 'function') {
            return false;
        }
        return window.pngtuberManager.playLayeredAnimation(target, options);
    }

    window.PNGTuberManager = PNGTuberManager;
    window.hideOtherAvatarRuntimesForPNGTuber = hideOtherAvatarRuntimesForPNGTuber;
    window.loadPNGTuberAvatar = loadPNGTuberAvatar;
    window.playPNGTuberAnimation = playPNGTuberAnimation;
})();
