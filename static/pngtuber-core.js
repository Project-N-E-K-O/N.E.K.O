(function () {
    'use strict';

    const DEFAULT_PLACEHOLDER = '/static/icons/default_character_card.png';
    const IMAGE_KEYS = ['idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'];
    const EMOTION_IMAGE_KEYS = {
        happy: 'happy_image',
        sad: 'sad_image',
        angry: 'angry_image',
        surprised: 'surprised_image'
    };
    const CLEAR_EMOTIONS = new Set(['neutral', 'idle', 'default', 'none', 'clear', '']);
    const DEFAULT_EMOTION_DURATION_MS = 5000;
    const SCALE_MIN = 0.1;
    const SCALE_MAX = 5;
    const REMIX_FRAME_SPEED_MULTIPLIER = 4;
    const REMIX_EYE_POINTER_FOLLOW_MULTIPLIER = 6.5;
    const REMIX_BLINK_POINTER_FOLLOW_MULTIPLIER = 3.0;
    const REMIX_EYE_POINTER_DELAY_MULTIPLIER = 0.55;
    const REMIX_BLINK_POINTER_DELAY_MULTIPLIER = 0.8;
    const REMIX_EYE_TARGET_FOLLOW_MULTIPLIER = 1.55;
    const REMIX_BLINK_TARGET_FOLLOW_MULTIPLIER = 1.15;
    const REMIX_LAYERED_CANVAS_PADDING_RATIO = 0.12;
    const REMIX_LAYERED_CANVAS_PADDING_MIN = 48;
    const REMIX_LAYERED_CANVAS_PADDING_MAX = 160;
    const REMIX_MESH_DEFORM_STRENGTH = 0.28;
    const PNGTUBER_PLUS_VISIBLE_VALUES = new Set([0, 10, 20, 30, 1, 21, 12, 32, 3, 13, 4, 15, 26, 36, 27, 38]);

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

    function isPNGTuberPlusLayerVisible(showTalk, showBlink, speaking, blinking) {
        const value = (Number(showTalk) || 0)
            + ((Number(showBlink) || 0) * 3)
            + (speaking ? 10 : 0)
            + (blinking ? 20 : 0);
        return PNGTUBER_PLUS_VISIBLE_VALUES.has(value);
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

    function isPngtuberMobileWebPage() {
        if (isModelManagerPage()) return false;
        if (document.body?.classList.contains('electron-chat-window')) return false;
        if (window.__LANLAN_IS_ELECTRON_PET__) return false;
        if (typeof window.isMobileWidth === 'function') return window.isMobileWidth();
        return window.innerWidth <= 768;
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
        normalized.offset_x = Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0;
        normalized.offset_y = Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0;
        normalized.mobile_scale = clampNumber(source.mobile_scale, SCALE_MIN, SCALE_MAX, Math.min(normalized.scale, 1));
        normalized.mobile_offset_x = Number.isFinite(Number(source.mobile_offset_x)) ? Number(source.mobile_offset_x) : 0;
        normalized.mobile_offset_y = Number.isFinite(Number(source.mobile_offset_y)) ? Number(source.mobile_offset_y) : 0;
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
            this._fallbackLayersBySpriteId = new Map();
            this._fallbackLayersBySpriteIdSource = null;
            this.layeredBlinking = false;
            this.layeredAssetVisibility = new Map();
            this.layeredAssetActionActive = false;
            this.layeredBlinkTimer = null;
            this.layeredBlinkEndTimer = null;
            this.layeredStateIndex = 0;
            this.layeredStateReturnTimer = null;
            this.layeredToggleVisibility = new Map();
            this.layeredLayerById = new Map();
            this.layeredAnimationFrame = null;
            this.layeredAnimationStart = 0;
            this.layeredCanvasPadding = 0;
            this.layeredBreathingFrame = null;
            this.layeredBreathingStart = 0;
            this.layeredPhysicsByLayer = new Map();
            this.remixModelMotionState = null;
            this.layeredDragVelocity = { x: 0, y: 0, at: 0 };
            this.layeredPointer = { x: 0, y: 0, targetX: 0, targetY: 0, active: false, at: 0, lastTime: 0 };
            this._boundLayeredHotkey = (event) => this.handleLayeredHotkey(event);
            this._boundLayeredPlayEvent = (event) => this.handleLayeredPlayEvent(event);
            this._boundLayeredPointerMove = (event) => this.handleLayeredPointerMove(event);
            this._layeredHotkeysAttached = false;
            this._layeredPlayEventAttached = false;
            this._layeredPointerAttached = false;
            this.state = 'idle';
            this.currentEmotion = null;
            this.emotionImage = '';
            this.emotionTimer = null;
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
            this.lipSyncFrame = null;
            this.lipSyncMouthOpen = 0;
            this.lipSyncMouthState = false;
            this.lipSyncLastStateChangeAt = 0;
            this.lipSyncNextPulseAt = 0;
            this.lipSyncPulseCloseAt = 0;
            this.talkingHopFrame = null;
            this.talkingHopStart = 0;
            this.talkingHopAmplitude = 0;
            this.talkingHopPeriodMs = 0;
            this.lastOverlayPositionUpdateAt = 0;
            this.lastAnimationTransformAt = 0;
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
            this._mouseTrackingEnabled = window.mouseTrackingEnabled !== false;
            this._pngtuberFloatingControlsVisible = true;
            this._pngtuberControlsHover = false;
            this._pngtuberHideButtonsTimer = null;
            this._pngtuberPointerEvaluateFrame = null;
            this._lastPngtuberPointerX = null;
            this._lastPngtuberPointerY = null;
            this._renderingPaused = false;
        }

        setMouseTrackingEnabled(enabled) {
            this._mouseTrackingEnabled = enabled !== false;
            window.mouseTrackingEnabled = this._mouseTrackingEnabled;
            if (this._mouseTrackingEnabled) {
                this.attachLayeredPointerTracking();
            } else {
                this.detachLayeredPointerTracking();
                this.resetLayeredPointerTracking();
                if (this.isLayeredActive()) {
                    this.startLayeredAnimationLoop({ preserveTimeline: true });
                }
            }
        }

        isMouseTrackingEnabled() {
            return this._mouseTrackingEnabled !== false;
        }

        resetLayeredPointerTracking() {
            this.layeredPointer = { x: 0, y: 0, targetX: 0, targetY: 0, active: false, at: 0, lastTime: 0 };
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
            this.stopLayeredBreathingLoop();
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

        pauseRendering() {
            this._renderingPaused = true;
            this.clearLayeredTimers();
            if (this.speakingMouthTimer) {
                clearTimeout(this.speakingMouthTimer);
                this.speakingMouthTimer = null;
            }
            if (this.returnIdleTimer) {
                clearTimeout(this.returnIdleTimer);
                this.returnIdleTimer = null;
            }
            if (this.clickTimer) {
                clearTimeout(this.clickTimer);
                this.clickTimer = null;
            }
            if (this.lipSyncFrame) {
                cancelAnimationFrame(this.lipSyncFrame);
                this.lipSyncFrame = null;
            }
            this.lipSyncMouthOpen = 0;
            this.lipSyncMouthState = false;
            this.lipSyncLastStateChangeAt = 0;
            this.lipSyncNextPulseAt = 0;
            this.lipSyncPulseCloseAt = 0;
            this.speakingMouthOpen = false;
            this.stopTalkingHopAnimation();
            this.stopSpeakingBounceAnimation();
        }

        resumeRendering() {
            if (!this._renderingPaused) return;
            this._renderingPaused = false;
            const container = this.container || document.getElementById(this.containerId);
            if (!container || container.style.display === 'none' ||
                (container.classList && container.classList.contains('hidden'))) {
                return;
            }
            if (this.isLayeredActive()) {
                this.drawLayeredState(this.state || 'idle');
                if (!this.layeredBlinkTimer && !this.layeredBlinkEndTimer) {
                    this.startLayeredBlinkLoop();
                }
                this.startLayeredAnimationLoop({ preserveTimeline: true });
            }
            if (this.isSpeaking) {
                this.startSpeakingMouthAnimation();
            }
        }

        stopLayeredAnimationLoop() {
            if (this.layeredAnimationFrame) {
                cancelAnimationFrame(this.layeredAnimationFrame);
                this.layeredAnimationFrame = null;
            }
        }

        attachLayeredHotkeys() {
            if (this._layeredHotkeysAttached || !this.isLayeredActive()) return;
            const toggles = this.layeredToggleEntries();
            if (toggles.length === 0
                && this.getLayeredStateCount() <= 1 && !this.hasLayeredAssetActions()) return;
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

        attachLayeredPointerTracking() {
            if (this._layeredPointerAttached || !this.isMouseTrackingEnabled() || !this.isLayeredActive() || !this.hasLayeredPointerTracking()) return;
            const eventName = window.PointerEvent ? 'pointermove' : 'mousemove';
            window.addEventListener(eventName, this._boundLayeredPointerMove, { passive: true });
            this._layeredPointerAttached = true;
            this._layeredPointerEventName = eventName;
        }

        detachLayeredPointerTracking() {
            if (!this._layeredPointerAttached) return;
            window.removeEventListener(this._layeredPointerEventName || 'pointermove', this._boundLayeredPointerMove);
            this._layeredPointerAttached = false;
            this._layeredPointerEventName = '';
        }

        handleLayeredPointerMove(event) {
            if (!this.isMouseTrackingEnabled()) return;
            if (!this.isLayeredActive() || !this.canvasElement || typeof this.canvasElement.getBoundingClientRect !== 'function') return;
            const rect = this.canvasElement.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            const targetX = ((event.clientX - (rect.left + rect.width / 2)) / (rect.width / 2));
            const targetY = ((event.clientY - (rect.top + rect.height / 2)) / (rect.height / 2));
            this.layeredPointer.targetX = Math.max(-1, Math.min(1, targetX));
            this.layeredPointer.targetY = Math.max(-1, Math.min(1, targetY));
            this.layeredPointer.clientX = event.clientX;
            this.layeredPointer.clientY = event.clientY;
            this.layeredPointer.active = true;
            this.layeredPointer.at = performance.now();
            this.startLayeredAnimationLoop({ preserveTimeline: true });
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

        normalizeLayeredTargetName(value) {
            return String(value ?? '').trim().toLowerCase().replace(/[\s_-]+/g, '');
        }

        layeredStateCatalog() {
            const catalog = this.layeredMetadata && this.layeredMetadata.state_catalog;
            return Array.isArray(catalog) ? catalog : [];
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
            const normalized = this.normalizeLayeredTargetName(text);
            const catalogMatch = this.layeredStateCatalog().find((record) => {
                const aliases = Array.isArray(record.aliases) ? record.aliases : [];
                return [record.name, record.label, record.hotkey, ...aliases].some((value) => {
                    return this.normalizeLayeredTargetName(value) === normalized;
                });
            });
            if (catalogMatch) {
                return Math.max(0, Math.min(stateCount - 1, Number(catalogMatch.state_index) || 0));
            }
            const hotkeys = Array.isArray(this.layeredMetadata?.hotkeys) ? this.layeredMetadata.hotkeys : [];
            const matched = hotkeys.find((hotkey) => {
                return this.normalizeLayeredTargetName(hotkey.key) === normalized
                    || this.normalizeLayeredTargetName(hotkey.label) === normalized
                    || this.normalizeLayeredTargetName(hotkey.name) === normalized;
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
            const previousIndex = this.layeredStateIndex;
            if (this.layeredStateReturnTimer) {
                clearTimeout(this.layeredStateReturnTimer);
                this.layeredStateReturnTimer = null;
            }
            this.layeredStateIndex = nextIndex;
            this.drawLayeredState();
            this.restartLayeredAnimationLoop();
            if ((options.source === 'hotkey' || options.source === 'alt-one-cycle-hotkey')
                && previousIndex !== nextIndex
                && this.isLayeredPlusModel()
                && this.layeredRuntimeFeatureEnabled('costume_change_bounce')) {
                this.startCostumeChangeHopAnimation();
            }
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
            if (this._renderingPaused) return false;
            if (!this.isLayeredActive()) return false;
            return this.setLayeredStateIndex(this.resolveLayeredAnimationTarget(target), {
                returnToDefaultAfterMs: options.returnToDefaultAfterMs,
                source: options.source || 'api'
            });
        }

        layeredEmotionTarget(emotionName) {
            const mappings = this.layeredMetadata && this.layeredMetadata.emotion_mappings;
            if (mappings && typeof mappings === 'object') {
                const mapping = mappings[emotionName];
                if (typeof mapping === 'number' && Number.isFinite(mapping)) return mapping;
                if (mapping && typeof mapping === 'object' && Number.isFinite(Number(mapping.state_index))) {
                    return Number(mapping.state_index);
                }
            }
            const fallbackOrder = { happy: 1, sad: 2, angry: 3, surprised: 4 };
            const fallbackIndex = fallbackOrder[emotionName];
            // The state-count fallback targets Remix's neutral/happy/sad/angry/
            // surprised state ordering. Plus imports always expose 10 costume
            // states that are not emotions, so skip the fallback for them —
            // Plus models only drive emotions when they ship explicit
            // emotion_mappings.
            if (fallbackIndex !== undefined && !this.isLayeredPlusModel() && this.getLayeredStateCount() >= 5) return fallbackIndex;
            return null;
        }

        layeredEmotionSupported() {
            const mappings = this.layeredMetadata && this.layeredMetadata.emotion_mappings;
            if (mappings && typeof mappings === 'object') {
                return Object.keys(mappings).some((emotion) => !CLEAR_EMOTIONS.has(this.normalizeEmotionName(emotion)));
            }
            return !this.isLayeredPlusModel() && this.getLayeredStateCount() >= 5;
        }

        normalizedLayeredEventKey(event) {
            return String(event?.key || '').trim().toLowerCase();
        }

        normalizeLayeredToggleKey(key) {
            const text = String(key || '').trim();
            return text && !['null', 'none'].includes(text.toLowerCase()) ? text : '';
        }

        layeredToggleEntries() {
            const toggles = this.layeredMetadata && this.layeredMetadata.toggles;
            if (!toggles || typeof toggles !== 'object') return [];
            if (Array.isArray(toggles)) {
                return toggles
                    .map((entry) => {
                        const key = this.normalizeLayeredToggleKey(entry?.key);
                        const layerIds = Array.isArray(entry?.layer_ids) ? entry.layer_ids : [];
                        return key ? { key, layerIds: layerIds.map((id) => String(id)) } : null;
                    })
                    .filter(Boolean);
            }
            return Object.entries(toggles)
                .map(([key, layerIds]) => {
                    const normalizedKey = this.normalizeLayeredToggleKey(key);
                    const ids = Array.isArray(layerIds) ? layerIds : [layerIds];
                    return normalizedKey ? { key: normalizedKey, layerIds: ids.map((id) => String(id)) } : null;
                })
                .filter(Boolean);
        }

        layeredToggleEntriesForEvent(event) {
            // Imported toggle keys are bare keypresses (e.g. "1".."0"). Reserved
            // runtime shortcuts (Alt+1 cycle, Alt+2 asset action) share those base
            // keys, so ignore modified events here — otherwise a toggle bound to
            // "1"/"2" would swallow Alt+1/Alt+2 before the reserved checks run.
            if (event.altKey || event.ctrlKey || event.metaKey) return [];
            const eventKey = this.normalizedLayeredEventKey(event);
            if (!eventKey) return [];
            return this.layeredToggleEntries().filter((entry) => String(entry.key || '').toLowerCase() === eventKey);
        }

        initializeLayeredToggleState(layers) {
            this.layeredToggleVisibility = new Map();
            this.layeredLayerById = new Map();
            (Array.isArray(layers) ? layers : []).forEach((layer) => {
                this.layerIdentityKeys(layer).forEach((id) => {
                    this.layeredLayerById.set(id, layer);
                });
                const toggle = this.normalizeLayeredToggleKey(layer.toggle || layer.state?.toggle);
                if (toggle) {
                    const id = this.primaryLayerId(layer);
                    if (id) this.layeredToggleVisibility.set(id, true);
                }
            });
            this.layeredToggleEntries().forEach((entry) => {
                entry.layerIds.forEach((id) => this.layeredToggleVisibility.set(String(id), true));
            });
        }

        primaryLayerId(layer) {
            return this.layerIdentityKeys(layer)[0] || '';
        }

        layerIdentityKeys(layer) {
            return [
                layer?.identification,
                layer?.sprite_id,
                layer?.id,
                layer?.key,
                layer?.order
            ]
                .filter((value) => value !== undefined && value !== null && String(value).trim() !== '')
                .map((value) => String(value));
        }

        isLayerToggleVisible(layer) {
            const ids = this.layerIdentityKeys(layer);
            return !ids.some((id) => this.layeredToggleVisibility.get(id) === false);
        }

        layerToggleAncestors(layer) {
            if (Array.isArray(layer?.parent_chain)) {
                return layer.parent_chain.map((id) => String(id)).filter(Boolean);
            }
            const chain = [];
            const visited = new Set(this.layerIdentityKeys(layer));
            let parentId = layer?.parent_id ?? layer?.parentId;
            while (parentId !== undefined && parentId !== null && String(parentId).trim() !== '') {
                const id = String(parentId);
                if (visited.has(id)) break;
                visited.add(id);
                chain.push(id);
                const parent = this.layeredLayerById.get(id);
                if (!parent) break;
                parentId = parent.parent_id ?? parent.parentId;
            }
            return chain;
        }

        hasHiddenLayeredToggleAncestor(layer) {
            return this.layerToggleAncestors(layer).some((id) => this.layeredToggleVisibility.get(id) === false);
        }

        toggleLayeredVisibilityForEvent(event) {
            const entries = this.layeredToggleEntriesForEvent(event);
            if (entries.length === 0) return false;
            entries.forEach((entry) => {
                entry.layerIds.forEach((id) => {
                    const key = String(id);
                    const current = this.layeredToggleVisibility.get(key);
                    this.layeredToggleVisibility.set(key, current === false);
                });
            });
            return true;
        }

        isLayeredCycleHotkey(event) {
            return !!(
                event
                && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
                && (event.key === '1' || event.code === 'Digit1' || event.keyCode === 49)
            );
        }

        cycleLayeredState() {
            if (!this.isLayeredActive() || this.getLayeredStateCount() <= 1) return false;
            const stateCount = this.getLayeredStateCount();
            return this.setLayeredStateIndex((this.layeredStateIndex + 1) % stateCount, { source: 'alt-one-cycle-hotkey' });
        }

        isLayeredAssetActionHotkey(event) {
            return !!(
                event
                && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
                && (event.key === '2' || event.code === 'Digit2' || event.keyCode === 50)
            );
        }

        hasLayeredAssetActions() {
            return Array.isArray(this.layeredMetadata?.asset_actions) && this.layeredMetadata.asset_actions.length > 0;
        }

        primaryLayeredAssetAction() {
            if (!this.hasLayeredAssetActions()) return null;
            return this.layeredMetadata.asset_actions.find((action) => {
                return Array.isArray(action.show_sprite_ids) && action.show_sprite_ids.length > 0;
            }) || this.layeredMetadata.asset_actions[0];
        }

        togglePrimaryLayeredAssetAction() {
            if (!this.isLayeredActive()) return false;
            const action = this.primaryLayeredAssetAction();
            if (!action) return false;
            this.layeredAssetActionActive = !this.layeredAssetActionActive;
            this.layeredAssetVisibility.clear();
            if (this.layeredAssetActionActive) {
                (action.show_sprite_ids || []).forEach((spriteId) => {
                    this.layeredAssetVisibility.set(String(spriteId), true);
                });
                (action.hide_sprite_ids || []).forEach((spriteId) => {
                    this.layeredAssetVisibility.set(String(spriteId), false);
                });
            }
            this.drawLayeredState();
            this.restartLayeredAnimationLoop();
            window.dispatchEvent(new CustomEvent('pngtuber-layered-asset-action-changed', {
                detail: {
                    active: this.layeredAssetActionActive,
                    action: action.key || action.label || '',
                    source: 'alt-two-asset-hotkey'
                }
            }));
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
            const hasToggleMatch = this.layeredToggleEntriesForEvent(event).length > 0;
            if (hasToggleMatch) {
                event.preventDefault();
                event.stopPropagation();
                if (this.toggleLayeredVisibilityForEvent(event)) {
                    this.drawLayeredState();
                    this.restartLayeredAnimationLoop();
                }
                return;
            }
            if (this.isLayeredCycleHotkey(event)) {
                event.preventDefault();
                event.stopPropagation();
                this.cycleLayeredState();
                return;
            }
            if (this.isLayeredAssetActionHotkey(event)) {
                event.preventDefault();
                event.stopPropagation();
                this.togglePrimaryLayeredAssetAction();
            }
        }

        async setupLayeredAdapter() {
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            this.detachLayeredPlayEvent();
            this.detachLayeredPointerTracking();
            this.layeredMetadata = null;
            this.layeredImages = new Map();
            this._fallbackLayersBySpriteId = new Map();
            this._fallbackLayersBySpriteIdSource = null;
            this.layeredStateIndex = 0;
            this.layeredToggleVisibility = new Map();
            this.layeredLayerById = new Map();
            this.layeredCanvasPadding = 0;
            this.layeredPhysicsByLayer = new Map();
            this.remixModelMotionState = null;
            this.layeredDragVelocity = { x: 0, y: 0, at: 0 };
            this.layeredPointer = { x: 0, y: 0, targetX: 0, targetY: 0, active: false, at: 0, lastTime: 0 };
            this.layeredAssetVisibility = new Map();
            this.layeredAssetActionActive = false;
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
                this.initializeLayeredToggleState(layers);
                this.ensureContainer();
                const canvas = this.canvasElement;
                const canvasInfo = metadata.canvas || {};
                const baseCanvasWidth = Math.max(1, Number(canvasInfo.width) || 1);
                const baseCanvasHeight = Math.max(1, Number(canvasInfo.height) || 1);
                this.layeredCanvasPadding = Math.max(
                    REMIX_LAYERED_CANVAS_PADDING_MIN,
                    Math.min(
                        REMIX_LAYERED_CANVAS_PADDING_MAX,
                        Math.ceil(Math.max(baseCanvasWidth, baseCanvasHeight) * REMIX_LAYERED_CANVAS_PADDING_RATIO)
                    )
                );
                canvas.width = baseCanvasWidth + this.layeredCanvasPadding * 2;
                canvas.height = baseCanvasHeight + this.layeredCanvasPadding * 2;
                canvas.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
                this.startLayeredBlinkLoop();
                this.restartLayeredAnimationLoop();
                this.attachLayeredHotkeys();
                this.attachLayeredPlayEvent();
                this.attachLayeredPointerTracking();
                return true;
            } catch (error) {
                console.warn('[PNGTuber] layered adapter disabled, falling back to image mode:', error);
                this.layeredMetadata = null;
                this.layeredImages = new Map();
                this.layeredCanvasPadding = 0;
                this.layeredToggleVisibility = new Map();
                this.layeredLayerById = new Map();
                this.detachLayeredPointerTracking();
                this._fallbackLayersBySpriteId = new Map();
                this._fallbackLayersBySpriteIdSource = null;
                return false;
            }
        }

        hasBlinkLayers() {
            const layers = this.layeredMetadata && Array.isArray(this.layeredMetadata.layers)
                ? this.layeredMetadata.layers
                : [];
            return layers.some((layer) => {
                if (Number(layer.showBlink || 0) !== 0) return true;
                // Scan every state, not just the default one: a model may only
                // blink in a non-default state reached via Alt+1/emotion, and
                // checking layer.state alone would leave its blink loop disabled.
                const states = Array.isArray(layer.states) ? layer.states : [];
                if (states.some((state) => !!(state && state.should_blink))) return true;
                return !!(layer.state && layer.state.should_blink);
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
            const assetVisibility = this.layeredAssetVisibility.get(String(layer.sprite_id));
            const assetForcedVisible = assetVisibility === true;
            if (assetVisibility === false) return false;
            if (layer.inactive_asset_ancestor && !assetForcedVisible) return false;
            const mode = stateName === 'talking' ? 'talking' : 'idle';
            const layerState = this.layerStateForCurrentIndex(layer);
            if (layerState.folder) return false;
            if (layerState.visible === false && !assetForcedVisible) return false;
            if (layerState.ancestor_visible === false && !assetForcedVisible) return false;
            if (layerState.ancestor_visible === undefined && layer.ancestor_visible === false && !assetForcedVisible) return false;
            if (!this.isLayerToggleVisible(layer)) return false;
            if (this.hasHiddenLayeredToggleAncestor(layer)) return false;
            if (!isPNGTuberPlusLayerVisible(layer.showTalk, layer.showBlink, mode === 'talking', this.layeredBlinking)) return false;

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

        isLayeredRemixModel() {
            return this.isLayeredActive()
                && (this.config.source_format === 'pngtube_remix_pngremix'
                    || this.layeredMetadata?.source_format === 'pngtube_remix_pngremix');
        }

        isLayeredPlusModel() {
            return this.isLayeredActive()
                && (this.config.source_format === 'pngtuber_plus_save'
                    || this.layeredMetadata?.source_format === 'pngtuber_plus_save'
                    || this.layeredMetadata?.source_format === 'pngtuber-plus');
        }

        layeredRuntimeFeatureEnabled(featureName) {
            const features = this.layeredMetadata && this.layeredMetadata.runtime_features;
            if (!features || typeof features !== 'object') return false;
            return features[featureName] === true;
        }

        stateHasMotion(layerState) {
            const layerMotionEnabled = this.layeredRuntimeFeatureEnabled('layer_motion');
            const spriteSheetEnabled = this.layeredRuntimeFeatureEnabled('sprite_sheet_animation');
            const hasXMotion = layerMotionEnabled
                && Math.abs(Number(layerState.xAmp) || 0) > 0.0001
                && Math.abs(Number(layerState.xFrq) || 0) > 0.0001;
            const hasYMotion = layerMotionEnabled
                && Math.abs(Number(layerState.yAmp) || 0) > 0.0001
                && Math.abs(Number(layerState.yFrq) || 0) > 0.0001;
            const hasWiggleMotion = layerMotionEnabled
                && Math.abs(Number(layerState.wiggle_amp) || 0) > 0.0001
                && Math.abs(Number(layerState.wiggle_freq || layerState.rot_frq) || 0) > 0.0001;
            const hasFrameAnimation = spriteSheetEnabled && this.stateHasFrameAnimation(layerState);
            return hasXMotion || hasYMotion || hasWiggleMotion || this.stateHasRemixLayerOscillation(layerState) || hasFrameAnimation;
        }

        stateHasRemixLayerOscillation(layerState) {
            if (!layerState || !layerState.physics) return false;
            return (Math.abs(Number(layerState.rot_frq) || 0) > 0.0001
                    && Math.abs(Number(layerState.rdragStr) || 0) > 0.0001)
                || (!!layerState.should_rotate && Math.abs(Number(layerState.should_rot_speed) || 0) > 0.0001);
        }

        stateHasRemixPhysics(layerState) {
            if (!layerState) return false;
            if (this.stateHasRemixMouseFollow(layerState)) return true;
            if (this.layeredRuntimeFeatureEnabled('physics_v2')
                && (this.remixValue(layerState, 'tip_point', null) !== null
                    || Math.abs(this.remixNumber(layerState, 'mesh_phys_x', 0)) > 0.0001
                    || Math.abs(this.remixNumber(layerState, 'mesh_phys_y', 0)) > 0.0001
                    || Math.abs(this.remixNumber(layerState, 'chain_rot_min', 0)) > 0.0001
                    || Math.abs(this.remixNumber(layerState, 'chain_rot_max', 0)) > 0.0001
                    || this.remixBool(layerState, 'drag_snap'))) return true;
            if (!layerState.physics && !layerState.wiggle && !layerState.wiggle_physics) return false;
            return Math.abs(Number(layerState.rdragStr) || 0) > 0.0001
                || Math.abs(Number(layerState.dragSpeed) || 0) > 0.0001
                || Math.abs(Number(layerState.stretchAmount) || 0) > 0.0001
                || Math.abs(Number(layerState.rot_frq) || 0) > 0.0001;
        }

        plusStateHasPhysics(layerState) {
            if (!layerState || !this.isLayeredPlusModel()) return false;
            return Math.abs(Number(layerState.xAmp) || 0) > 0.0001
                || Math.abs(Number(layerState.yAmp) || 0) > 0.0001
                || Math.abs(Number(layerState.rdragStr) || 0) > 0.0001
                || Math.abs(Number(layerState.dragSpeed) || 0) > 0.0001
                || Math.abs(Number(layerState.stretchAmount) || 0) > 0.0001
                || this.stateHasFrameAnimation(layerState);
        }

        remixStatePrefix() {
            if ((this.state || 'idle') === 'scream') return 'scream_';
            if ((this.state || 'idle') === 'talking') return 'mo_';
            return '';
        }

        remixDirectValue(layerState, key) {
            if (!layerState) return undefined;
            const prefix = layerState.shared_movement ? '' : this.remixStatePrefix();
            const prefixedKey = `${prefix}${key}`;
            return prefix && Object.prototype.hasOwnProperty.call(layerState, prefixedKey)
                ? layerState[prefixedKey]
                : layerState[key];
        }

        remixLegacyFollowValue(layerState, key) {
            if (!layerState || layerState.updated_follow_movement === true) return undefined;
            const legacyNumber = (legacyKey) => {
                const value = Number(this.remixDirectValue(layerState, legacyKey));
                return Number.isFinite(value) ? value : 0;
            };
            const hasLegacyMotion = (legacyKeys) => legacyKeys
                .some((legacyKey) => Math.abs(legacyNumber(legacyKey)) > 0.0001);

            if ((key === 'pos_x_min' || key === 'pos_x_max') && hasLegacyMotion(['look_at_mouse_pos'])) {
                const value = Math.abs(legacyNumber('look_at_mouse_pos'));
                return key === 'pos_x_min' ? -value : value;
            }
            if ((key === 'pos_y_min' || key === 'pos_y_max') && hasLegacyMotion(['look_at_mouse_pos_y'])) {
                const value = Math.abs(legacyNumber('look_at_mouse_pos_y'));
                return key === 'pos_y_min' ? -value : value;
            }
            if (key === 'pos_invert_x' && hasLegacyMotion(['look_at_mouse_pos'])) {
                return legacyNumber('look_at_mouse_pos') < 0;
            }
            if (key === 'pos_invert_y' && hasLegacyMotion(['look_at_mouse_pos_y'])) {
                return legacyNumber('look_at_mouse_pos_y') < 0;
            }
            if ((key === 'rot_min' || key === 'rot_max') && hasLegacyMotion(['mouse_rotation', 'mouse_rotation_max'])) {
                return key === 'rot_min' ? legacyNumber('mouse_rotation') : legacyNumber('mouse_rotation_max');
            }
            if ((key === 'scale_x_min' || key === 'scale_x_max') && hasLegacyMotion(['mouse_scale_x', 'mouse_scale_x_max'])) {
                return key === 'scale_x_min' ? legacyNumber('mouse_scale_x') : legacyNumber('mouse_scale_x_max');
            }
            if ((key === 'scale_y_min' || key === 'scale_y_max') && hasLegacyMotion(['mouse_scale_y', 'mouse_scale_y_max'])) {
                return key === 'scale_y_min' ? legacyNumber('mouse_scale_y') : legacyNumber('mouse_scale_y_max');
            }
            return undefined;
        }

        remixValue(layerState, key, fallback = 0) {
            if (!layerState) return fallback;
            const legacyValue = this.remixLegacyFollowValue(layerState, key);
            if (legacyValue !== undefined) return legacyValue;
            const value = this.remixDirectValue(layerState, key);
            if (value !== undefined && value !== null) return value;
            return fallback;
        }

        remixNumber(layerState, key, fallback = 0) {
            const value = Number(this.remixValue(layerState, key, fallback));
            return Number.isFinite(value) ? value : fallback;
        }

        remixBool(layerState, key) {
            return !!this.remixValue(layerState, key, false);
        }

        remixIgnoresModelBounce(layerState) {
            return this.remixBool(layerState, 'ignore_bounce') && !this.remixBool(layerState, 'static_obj');
        }

        remixFollowType(layerState, key) {
            const value = Number(this.remixValue(layerState, key, 15));
            return Number.isFinite(value) ? value : 15;
        }

        remixHasLegacyFollowMotion(layerState, keys) {
            if (!layerState || layerState.updated_follow_movement === true) return false;
            return keys.some((key) => Math.abs(Number(this.remixDirectValue(layerState, key)) || 0) > 0.0001);
        }

        remixPositionFollowsMouse(layerState) {
            return this.remixFollowType(layerState, 'follow_type') === 0
                || this.remixHasLegacyFollowMotion(layerState, ['look_at_mouse_pos', 'look_at_mouse_pos_y']);
        }

        remixRotationFollowsMouse(layerState) {
            return this.remixFollowType(layerState, 'follow_type2') === 0
                || this.remixHasLegacyFollowMotion(layerState, ['mouse_rotation', 'mouse_rotation_max']);
        }

        remixScaleFollowsMouse(layerState) {
            return this.remixFollowType(layerState, 'follow_type3') === 0
                || this.remixHasLegacyFollowMotion(layerState, ['mouse_scale_x', 'mouse_scale_x_max', 'mouse_scale_y', 'mouse_scale_y_max']);
        }

        stateHasRemixMouseFollow(layerState) {
            if (!layerState) return false;
            return this.remixPositionFollowsMouse(layerState)
                || this.remixRotationFollowsMouse(layerState)
                || this.remixScaleFollowsMouse(layerState);
        }

        stateHasAnimateToMouseSheet(layerState) {
            if (!layerState) return false;
            return layerState.non_animated_sheet === true
                && this.remixBool(layerState, 'animate_to_mouse')
                && (Math.max(1, Math.floor(Number(layerState.hframes) || 1)) > 1
                    || Math.max(1, Math.floor(Number(layerState.vframes) || 1)) > 1);
        }

        stateHasPointerTracking(layerState) {
            return this.stateHasRemixMouseFollow(layerState) || this.stateHasAnimateToMouseSheet(layerState);
        }

        hasLayeredPointerTracking() {
            if (!this.layeredMetadata || !Array.isArray(this.layeredMetadata.layers)) return false;
            return this.layeredMetadata.layers.some((layer) => {
                const states = Array.isArray(layer.states) ? layer.states : [];
                if (states.some((state) => this.stateHasPointerTracking(state))) return true;
                return this.stateHasPointerTracking(layer.state || {});
            });
        }

        stateFrameInfo(layer, layerState, img, timestamp = performance.now(), overrideFrame = null) {
            const imageWidth = Number(layer.image_width || img.width) || img.width;
            const imageHeight = Number(layer.image_height || img.height) || img.height;
            const hframes = Math.max(1, Math.floor(Number(layerState.hframes) || Number(layer.hframes) || 1));
            const vframes = Math.max(1, Math.floor(Number(layerState.vframes) || Number(layer.vframes) || 1));
            const declaredFrames = Math.floor(Number(layerState.frames) || Number(layer.frames) || hframes * vframes);
            const frames = Math.max(1, declaredFrames);
            const rows = Math.max(vframes, Math.ceil(frames / hframes));
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
            let frame = overrideFrame === null || overrideFrame === undefined
                ? Math.max(0, Math.floor(Number(layerState.frame) || 0))
                : Math.max(0, Math.floor(Number(overrideFrame) || 0));
            const speed = Math.max(0, Number(layerState.animation_speed) || Number(layer.animation_speed) || 0);
            const canAnimate = this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')
                && frames > 1
                && speed > 0
                && hasSheet
                && layerState.non_animated_sheet !== true;
            if (overrideFrame === null && canAnimate) {
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
                vframes,
                animated: canAnimate,
                legacyOffsetX: legacyFullSheetX ? (imageWidth - frameWidth) / 2 : 0,
                legacyOffsetY: legacyFullSheetY ? (imageHeight - frameHeight) / 2 : 0
            };
        }

        stateHasFrameAnimation(layerState) {
            if (this.stateHasAnimateToMouseSheet(layerState)) return true;
            const hframes = Math.max(1, Math.floor(Number(layerState.hframes) || 1));
            const vframes = Math.max(1, Math.floor(Number(layerState.vframes) || 1));
            const frames = Math.max(1, Math.floor(Number(layerState.frames) || hframes * vframes));
            const speed = Math.max(0, Number(layerState.animation_speed) || 0);
            return frames > 1
                && speed > 0
                && (hframes > 1 || vframes > 1 || Math.ceil(frames / hframes) > 1)
                && layerState.non_animated_sheet !== true;
        }

        hasMotionLayersForCurrentState(stateName = this.state || 'idle') {
            if (!this.isLayeredActive()) return false;
            if (this.stateHasModelMotion(stateName) || this.layeredPointerNeedsFrame()) return true;
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            return layers.some((layer) => {
                if (!this.shouldRenderLayer(layer, stateName)) return false;
                const layerState = this.layerStateForCurrentIndex(layer);
                return this.stateHasMotion(layerState)
                    || this.plusStateHasPhysics(layerState)
                    || (this.stateHasRemixPhysics(layerState)
                        && (this.layeredRuntimeFeatureEnabled('physics_v2') || this.layeredPhysicsNeedsFrame()));
            });
        }

        startLayeredAnimationLoop(options = {}) {
            if (this._renderingPaused) return;
            this.startLayeredBreathingLoop();
            if (this.layeredAnimationFrame) return;
            if (!this.isLayeredActive() || !this.hasMotionLayersForCurrentState()) return;
            if (!options.preserveTimeline || !this.layeredAnimationStart) {
                this.layeredAnimationStart = performance.now();
            }
            const tick = (timestamp) => {
                if (!this.isLayeredActive()) {
                    this.stopLayeredAnimationLoop();
                    return;
                }
                this.drawLayeredState(this.state || 'idle', timestamp);
                if (!this.hasMotionLayersForCurrentState()) {
                    this.stopLayeredAnimationLoop();
                    return;
                }
                this.layeredAnimationFrame = requestAnimationFrame(tick);
            };
            this.layeredAnimationFrame = requestAnimationFrame(tick);
        }

        restartLayeredAnimationLoop() {
            this.stopLayeredAnimationLoop();
            this.startLayeredAnimationLoop();
        }

        layeredBreathingEnabled() {
            if (!this.isLayeredActive()) return false;
            const features = this.layeredMetadata && this.layeredMetadata.runtime_features;
            if (features && typeof features === 'object' && features.layered_breathing === false) return false;
            return this.isLayeredActive();
        }

        updateOverlayPositionsForAnimation(timestamp = performance.now()) {
            const minIntervalMs = 120;
            if (this.lastOverlayPositionUpdateAt && timestamp - this.lastOverlayPositionUpdateAt < minIntervalMs) return;
            this.lastOverlayPositionUpdateAt = timestamp;
            this.updateLockIconPosition();
        }

        applyAnimationTransform(timestamp = performance.now()) {
            if (this.lastAnimationTransformAt === timestamp) return;
            this.lastAnimationTransformAt = timestamp;
            this.applyTransform(timestamp);
        }

        currentLayeredBreathingTransform(timestamp = performance.now()) {
            if (!this.layeredBreathingEnabled()) return { y: 0, scaleX: 1, scaleY: 1 };
            if (!this.layeredBreathingStart) return { y: 0, scaleX: 1, scaleY: 1 };
            const elapsedSeconds = Math.max(0, (timestamp - this.layeredBreathingStart) / 1000);
            const wave = (Math.sin(elapsedSeconds * Math.PI * 2 * 0.32) + 1) / 2;
            return {
                y: -2.6 * wave,
                scaleX: 1 + 0.004 * wave,
                scaleY: 1 + 0.009 * wave
            };
        }

        startLayeredBreathingLoop() {
            if (this._renderingPaused) return;
            if (this.layeredBreathingFrame || !this.layeredBreathingEnabled()) return;
            this.layeredBreathingStart = this.layeredBreathingStart || performance.now();
            const tick = (timestamp) => {
                if (!this.layeredBreathingEnabled() || !this.container || this.container.style.display === 'none') {
                    this.stopLayeredBreathingLoop();
                    return;
                }
                this.applyAnimationTransform(timestamp);
                this.updateOverlayPositionsForAnimation(timestamp);
                this.layeredBreathingFrame = requestAnimationFrame(tick);
            };
            this.layeredBreathingFrame = requestAnimationFrame(tick);
        }

        stopLayeredBreathingLoop() {
            if (this.layeredBreathingFrame) {
                cancelAnimationFrame(this.layeredBreathingFrame);
                this.layeredBreathingFrame = null;
            }
            this.layeredBreathingStart = 0;
        }

        remixTick(timestamp = performance.now()) {
            return Math.max(0, (timestamp - (this.layeredAnimationStart || timestamp)) / 1000);
        }

        motionValue(amplitude, frequency, timestamp, phase = 0) {
            const amp = Number(amplitude) || 0;
            const freq = Number(frequency) || 0;
            if (!amp || !freq) return 0;
            return Math.sin(this.remixTick(timestamp) * freq + phase) * amp;
        }

        remixStateMotionParams(stateName = this.state || 'idle') {
            const settings = this.currentRemixStateSettings();
            const key = stateName === 'talking' ? 'state_param_mo' : 'state_param_mc';
            const params = settings && typeof settings[key] === 'object' ? settings[key] : null;
            return params || {};
        }

        stateHasModelMotion(stateName = this.state || 'idle') {
            const settings = this.currentRemixStateSettings();
            const animation = String(stateName === 'talking' ? settings.current_mo_anim : (settings.current_mc_anim || '')).toLowerCase();
            if (animation.includes('bouncy') || animation.includes('bounce') || animation.includes('wobble') || animation.includes('float') || animation.includes('squish')) return true;
            const params = this.remixStateMotionParams(stateName);
            return (Math.abs(Number(params.xAmp) || 0) > 0.0001 && Math.abs(Number(params.xFrq) || 0) > 0.0001)
                || (Math.abs(Number(params.yAmp) || 0) > 0.0001 && Math.abs(Number(params.yFrq) || 0) > 0.0001);
        }

        modelMotionTransform(stateName = this.state || 'idle', timestamp = performance.now()) {
            if (!this.remixModelMotionState) {
                this.remixModelMotionState = { x: 0, y: 0, yVel: 0, lastTime: timestamp, lastState: '', oneBounceDone: false };
            }
            const state = this.remixModelMotionState;
            const previousState = state.lastState;
            if (previousState !== stateName) {
                state.lastState = stateName;
                state.oneBounceDone = false;
            }
            const dt = Math.max(0.001, Math.min(0.05, (timestamp - (state.lastTime || timestamp)) / 1000 || 1 / 60));
            state.lastTime = timestamp;
            const params = this.remixStateMotionParams(stateName);
            const settings = this.currentRemixStateSettings();
            const animation = String(stateName === 'talking' ? settings.current_mo_anim : (settings.current_mc_anim || '')).toLowerCase();
            const gravity = Number(params.bounce_gravity) || 575;
            const energy = Number(params.bounce_energy) || 250;
            const targetX = this.motionValue(params.xAmp, params.xFrq, timestamp, 0);
            const targetY = this.motionValue(params.yAmp, params.yFrq, timestamp, 0);

            if (animation.includes('wobble')) {
                state.x += (targetX - state.x) * 0.08;
                state.y += (targetY - state.y) * 0.08;
                state.yVel = 0;
            } else if (animation.includes('float')) {
                state.x += (0 - state.x) * 0.05;
                state.y += (targetY - state.y) * 0.08;
                state.yVel = 0;
            } else if (animation.includes('bouncy')) {
                if (state.y > -1) state.yVel = -energy;
                state.yVel = Math.max(-90000000, Math.min(90000000, state.yVel + gravity * dt));
                state.y = Math.min(0, state.y + state.yVel * dt);
                state.x += (0 - state.x) * 0.05;
            } else if (animation.includes('one bounce')) {
                if (!state.oneBounceDone && state.y > -16) {
                    state.yVel = -energy;
                    state.oneBounceDone = true;
                }
                state.yVel = Math.max(-90000000, Math.min(90000000, state.yVel + gravity * dt));
                state.y = Math.min(0, state.y + state.yVel * dt);
                state.x += (0 - state.x) * 0.05;
                if (state.y >= -0.01 && state.yVel > 0) {
                    state.y = 0;
                    state.yVel = 0;
                }
            } else if (animation.includes('squish')) {
                state.x += (0 - state.x) * 0.05;
                state.y += (targetY - state.y) * 0.08;
                state.yVel = 0;
            } else {
                state.x += (0 - state.x) * 0.05;
                state.y += (0 - state.y) * 0.05;
                state.yVel = 0;
            }
            return {
                x: state.x,
                y: state.y
            };
        }

        updateLayeredPointer(timestamp = performance.now(), delay = 0.1) {
            const pointer = this.layeredPointer || { x: 0, y: 0, targetX: 0, targetY: 0, active: false, at: 0, lastTime: 0 };
            if (!this.isMouseTrackingEnabled()) {
                pointer.targetX = 0;
                pointer.targetY = 0;
                pointer.clientX = undefined;
                pointer.clientY = undefined;
                pointer.active = false;
            }
            const lastTime = Number(pointer.lastTime) || timestamp;
            const dt = Math.max(0.001, Math.min(0.05, (timestamp - lastTime) / 1000 || 1 / 60));
            pointer.lastTime = timestamp;
            const speed = 1 / Math.max(0.025, Number(delay) || 0.1);
            const follow = 1 - Math.exp(-dt * speed);
            pointer.x += ((Number(pointer.targetX) || 0) - pointer.x) * follow;
            pointer.y += ((Number(pointer.targetY) || 0) - pointer.y) * follow;
            this.layeredPointer = pointer;
            return pointer;
        }

        remixPointerMaxPositionRange(layerState) {
            if (!layerState) return 0;
            return Math.max(
                Math.abs(this.remixNumber(layerState, 'pos_x_min', 0)),
                Math.abs(this.remixNumber(layerState, 'pos_x_max', 0)),
                Math.abs(this.remixNumber(layerState, 'pos_y_min', 0)),
                Math.abs(this.remixNumber(layerState, 'pos_y_max', 0))
            );
        }

        isRemixExplicitEyeLayer(layerState) {
            return !!layerState && (Number(layerState.follow_eye || 0) !== 0
                || Number(layerState.gaze_eye || 0) !== 0
                || Number(layerState.style_eye || 0) !== 0);
        }

        isRemixSmallRangeEyeLayer(layerState) {
            const maxPositionRange = this.remixPointerMaxPositionRange(layerState);
            return this.remixPositionFollowsMouse(layerState)
                && maxPositionRange > 0
                && maxPositionRange <= 6;
        }

        isRemixBlinkLayer(layerState) {
            return !!(layerState && (layerState.effective_should_blink ?? layerState.should_blink));
        }

        remixPointerFollowMultiplier(layerState) {
            if (this.isRemixExplicitEyeLayer(layerState) || this.isRemixSmallRangeEyeLayer(layerState)) {
                return REMIX_EYE_POINTER_FOLLOW_MULTIPLIER;
            }
            return this.isRemixBlinkLayer(layerState) ? REMIX_BLINK_POINTER_FOLLOW_MULTIPLIER : 1;
        }

        remixPointerDelay(layerState, delay) {
            const baseDelay = Math.max(0.025, Number(delay) || 0.1);
            if (this.isRemixExplicitEyeLayer(layerState) || this.isRemixSmallRangeEyeLayer(layerState)) {
                return Math.max(0.025, baseDelay * REMIX_EYE_POINTER_DELAY_MULTIPLIER);
            }
            if (this.isRemixBlinkLayer(layerState)) {
                return Math.max(0.025, baseDelay * REMIX_BLINK_POINTER_DELAY_MULTIPLIER);
            }
            return baseDelay;
        }

        remixTargetFollowMultiplier(layerState) {
            if (this.isRemixExplicitEyeLayer(layerState) || this.isRemixSmallRangeEyeLayer(layerState)) {
                return REMIX_EYE_TARGET_FOLLOW_MULTIPLIER;
            }
            return this.isRemixBlinkLayer(layerState) ? REMIX_BLINK_TARGET_FOLLOW_MULTIPLIER : 1;
        }

        layeredPointerForLayer(layer, layerState, frame, timestamp = performance.now(), delay = 0.1) {
            const pointer = this.updateLayeredPointer(timestamp, delay);
            const clientX = Number(pointer.clientX);
            const clientY = Number(pointer.clientY);
            if (!Number.isFinite(clientX) || !Number.isFinite(clientY) || !this.canvasElement) return pointer;
            const rect = typeof this.canvasElement.getBoundingClientRect === 'function'
                ? this.canvasElement.getBoundingClientRect()
                : null;
            if (!rect || !rect.width || !rect.height) return pointer;
            const canvasWidth = Math.max(1, Number(this.canvasElement.width) || Number(this.layeredMetadata?.canvas?.width) || 1);
            const canvasHeight = Math.max(1, Number(this.canvasElement.height) || Number(this.layeredMetadata?.canvas?.height) || 1);
            const padding = Number(this.layeredCanvasPadding) || 0;
            const frameWidth = Number(frame?.dw || layerState?.frame_width || layer?.width) || 1;
            const frameHeight = Number(frame?.dh || layerState?.frame_height || layer?.height) || 1;
            const baseX = (Number(layerState?.x ?? layer?.x) || 0) + (Number(frame?.legacyOffsetX) || 0) + padding;
            const baseY = (Number(layerState?.y ?? layer?.y) || 0) + (Number(frame?.legacyOffsetY) || 0) + padding;
            const layerCenterX = rect.left + ((baseX + frameWidth / 2) / canvasWidth) * rect.width;
            const layerCenterY = rect.top + ((baseY + frameHeight / 2) / canvasHeight) * rect.height;
            const followMultiplier = this.remixPointerFollowMultiplier(layerState);
            const normalizeAxis = (clientValue, centerValue, lowerEdge, upperEdge) => {
                const diff = clientValue - centerValue;
                if (Math.abs(diff) < 0.001) return 0;
                const space = diff >= 0
                    ? Math.max(1, upperEdge - centerValue)
                    : Math.max(1, centerValue - lowerEdge);
                return Math.max(-1, Math.min(1, (diff / space) * followMultiplier));
            };
            const rawX = clientX - layerCenterX;
            const rawY = clientY - layerCenterY;
            return {
                ...pointer,
                rawX,
                rawY,
                x: normalizeAxis(clientX, layerCenterX, 0, window.innerWidth || rect.right),
                y: normalizeAxis(clientY, layerCenterY, 0, window.innerHeight || rect.bottom)
            };
        }

        layeredPointerNeedsFrame(timestamp = performance.now()) {
            if (!this.isMouseTrackingEnabled()) return false;
            if (!this.hasLayeredPointerTracking()) return false;
            const pointer = this.layeredPointer || {};
            const age = Math.max(0, (timestamp - (Number(pointer.at) || timestamp)) / 1000);
            return !!pointer.active
                && age < 8
                && (Math.abs((Number(pointer.targetX) || 0) - (Number(pointer.x) || 0)) > 0.001
                    || Math.abs((Number(pointer.targetY) || 0) - (Number(pointer.y) || 0)) > 0.001);
        }

        updateLayeredDragVelocity(dx, dy, timestamp = performance.now()) {
            if (!this.isLayeredActive()) return;
            const lastAt = Number(this.layeredDragVelocity?.at) || timestamp;
            const dt = Math.max(0.008, Math.min(0.08, (timestamp - lastAt) / 1000 || 1 / 60));
            const invScale = 1 / Math.max(0.1, Number(this.config.scale) || 1);
            const nextX = Math.max(-1800, Math.min(1800, (Number(dx) || 0) * invScale / dt));
            const nextY = Math.max(-1800, Math.min(1800, (Number(dy) || 0) * invScale / dt));
            this.layeredDragVelocity = {
                x: Number.isFinite(nextX) ? nextX : 0,
                y: Number.isFinite(nextY) ? nextY : 0,
                at: timestamp
            };
            this.startLayeredAnimationLoop({ preserveTimeline: true });
        }

        currentLayeredDragVelocity(timestamp = performance.now()) {
            const velocity = this.layeredDragVelocity || { x: 0, y: 0, at: 0 };
            const age = Math.max(0, (timestamp - (Number(velocity.at) || timestamp)) / 1000);
            const decay = Math.exp(-age * 4.8);
            return {
                x: (Number(velocity.x) || 0) * decay,
                y: (Number(velocity.y) || 0) * decay
            };
        }

        layeredPhysicsNeedsFrame(timestamp = performance.now()) {
            if (this._dragState || this._touchZoomState) return true;
            const drag = this.currentLayeredDragVelocity(timestamp);
            if (Math.hypot(drag.x, drag.y) > 1) return true;
            for (const state of this.layeredPhysicsByLayer.values()) {
                if (Math.abs(Number(state.x) || 0) > 0.02
                    || Math.abs(Number(state.y) || 0) > 0.02
                    || Math.abs(Number(state.rotation) || 0) > 0.0005
                    || Math.abs(Number(state.stretch) || 0) > 0.0005
                    || Math.abs((Number(state.scaleX) || 1) - 1) > 0.0005
                    || Math.abs((Number(state.scaleY) || 1) - 1) > 0.0005) {
                    return true;
                }
            }
            return false;
        }

        layeredPhysicsKey(layer) {
            return `${layer.sprite_id ?? layer.order ?? 'layer'}:${layer.order ?? 0}`;
        }

        layeredPhysicsDepth(layer, layerState) {
            const chain = Array.isArray(layerState?.parent_chain)
                ? layerState.parent_chain
                : (Array.isArray(layer?.parent_chain) ? layer.parent_chain : []);
            return Math.max(0, chain.length - 1);
        }

        layeredPhysicsRelativePoint(layer, layerState, frame) {
            const canvas = this.layeredMetadata?.canvas || {};
            const canvasWidth = Math.max(1, Number(canvas.width) || this.canvasElement?.width || 1);
            const canvasHeight = Math.max(1, Number(canvas.height) || this.canvasElement?.height || 1);
            const width = Number(frame?.dw || layerState?.frame_width || layer?.width) || 1;
            const height = Number(frame?.dh || layerState?.frame_height || layer?.height) || 1;
            const x = (Number(layerState?.x ?? layer?.x) || 0) + width / 2;
            const y = (Number(layerState?.y ?? layer?.y) || 0) + height / 2;
            return {
                x: Math.max(-1, Math.min(1, (x - canvasWidth / 2) / (canvasWidth / 2))),
                y: Math.max(-1, Math.min(1, (y - canvasHeight / 2) / (canvasHeight / 2)))
            };
        }

        remixLayerOscillationRotation(layerState, timestamp = performance.now(), phase = 0) {
            if (!this.stateHasRemixLayerOscillation(layerState)) return 0;
            const rotFreq = this.remixNumber(layerState, 'rot_frq', 0);
            const rotationDegrees = this.motionValue(this.remixNumber(layerState, 'rdragStr', 0), rotFreq, timestamp, phase);
            const autoRotation = this.remixBool(layerState, 'should_rotate')
                ? this.remixTick(timestamp) * this.remixNumber(layerState, 'should_rot_speed', 0) * 60
                : 0;
            return (rotationDegrees + autoRotation) * Math.PI / 180;
        }

        moveToward(current, target, delta) {
            const step = Math.abs(Number(delta) || 0);
            if (Math.abs(target - current) <= step) return target;
            return current + Math.sign(target - current) * step;
        }

        updateAnimateToMouseFrame(layerState, frame, pointer, physicsState) {
            if (!this.stateHasAnimateToMouseSheet(layerState) || !frame || !pointer) return null;
            const hframes = Math.max(1, Math.floor(Number(frame.hframes) || Number(layerState.hframes) || 1));
            const vframes = Math.max(1, Math.floor(Number(frame.vframes) || Number(layerState.vframes) || 1));
            const rangeX = Math.abs(this.remixNumber(layerState, 'pos_x_max', 0));
            const rangeY = Math.abs(this.remixNumber(layerState, 'pos_y_max', 0));
            // One-dimensional sheets are valid: stateHasAnimateToMouseSheet()
            // enables sheets with only hframes > 1 or only vframes > 1 (e.g. a
            // horizontal look sheet with just pos_x_max). Bail only when neither
            // axis has a follow range; an axis with no range stays on frame 0.
            if (!rangeX && !rangeY) return null;
            const rawX = Number(pointer.rawX) || 0;
            const rawY = Number(pointer.rawY) || 0;
            const dist = Math.hypot(rawX, rawY);
            const dirX = dist > 0.0001 ? rawX / dist : 0;
            const dirY = dist > 0.0001 ? rawY / dist : 0;
            const normX = rangeX ? ((dirX * Math.min(dist, rangeX)) / (2 * rangeX)) + 0.5 : 0;
            const normY = rangeY ? ((dirY * Math.min(dist, rangeY)) / (2 * rangeY)) + 0.5 : 0;
            const targetFrameX = Math.max(0, Math.min(hframes - 1, Math.floor(normX * hframes)));
            const targetFrameY = Math.max(0, Math.min(vframes - 1, Math.floor(normY * vframes)));
            const speed = Math.max(0, this.remixNumber(layerState, 'animate_to_mouse_speed', 1));
            physicsState.frameH = this.moveToward(Number(physicsState.frameH ?? targetFrameX), targetFrameX, speed);
            physicsState.frameV = this.moveToward(Number(physicsState.frameV ?? targetFrameY), targetFrameY, speed);
            return Math.max(0, Math.min(frame.frames - 1, Math.floor(physicsState.frameV) * hframes + Math.floor(physicsState.frameH)));
        }

        layeredPhysicsTransform(layer, layerState, timestamp = performance.now(), frame = null) {
            const hasPhysics = this.stateHasRemixPhysics(layerState);
            const hasMouseSheet = this.stateHasAnimateToMouseSheet(layerState);
            if (!hasPhysics && !hasMouseSheet) {
                return { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, meshX: 0, meshY: 0, frame: null };
            }
            const key = this.layeredPhysicsKey(layer);
            let state = this.layeredPhysicsByLayer.get(key);
            if (!state) {
                state = {
                    x: 0,
                    y: 0,
                    targetX: 0,
                    targetY: 0,
                    velocityDistX: 0,
                    velocityDistY: 0,
                    rotation: 0,
                    stretch: 0,
                    scaleX: 1,
                    scaleY: 1,
                    frameH: null,
                    frameV: null,
                    lastMouseX: null,
                    lastMouseY: null,
                    lastTime: timestamp
                };
                this.layeredPhysicsByLayer.set(key, state);
            }

            const dt = Math.max(0.001, Math.min(0.05, (timestamp - (state.lastTime || timestamp)) / 1000 || 1 / 60));
            state.lastTime = timestamp;

            const drag = this.currentLayeredDragVelocity(timestamp);
            const dragSpeed = Math.max(0, this.remixNumber(layerState, 'dragSpeed', 0));
            const rotationStrength = this.remixNumber(layerState, 'rdragStr', 0);
            const stretchAmount = this.remixNumber(layerState, 'stretchAmount', 0);
            const physEff = this.remixNumber(layerState, 'phys_eff', 25);
            const mouseDelay = this.remixNumber(layerState, 'mouse_delay', 0.1);
            const pointerDelay = this.remixPointerDelay(layerState, mouseDelay);
            const targetFollowMultiplier = this.remixTargetFollowMultiplier(layerState);
            const pointer = this.stateHasPointerTracking(layerState)
                ? this.layeredPointerForLayer(layer, layerState, frame, timestamp, pointerDelay)
                : { x: 0, y: 0 };
            const followsMouseVelocity = this.remixBool(layerState, 'follow_mouse_velocity');
            let velocity = { mouseDeltaX: 0, mouseDeltaY: 0, distanceX: 0, distanceY: 0, distanceLength: 0 };
            const rawX = Number(pointer.rawX);
            const rawY = Number(pointer.rawY);
            if (Number.isFinite(rawX) && Number.isFinite(rawY)) {
                const lastMouseX = state.lastMouseX !== null && Number.isFinite(Number(state.lastMouseX)) ? Number(state.lastMouseX) : rawX;
                const lastMouseY = state.lastMouseY !== null && Number.isFinite(Number(state.lastMouseY)) ? Number(state.lastMouseY) : rawY;
                const mouseDeltaX = lastMouseX - rawX;
                const mouseDeltaY = lastMouseY - rawY;
                const distanceX = Math.tanh(mouseDeltaX);
                const distanceY = Math.tanh(mouseDeltaY);
                const distanceLength = Math.hypot(distanceX, distanceY);
                const velocityTargetX = -Math.sign(mouseDeltaX) * distanceLength * this.remixNumber(layerState, 'pos_x_max', 0);
                const velocityTargetY = -Math.sign(mouseDeltaY) * distanceLength * this.remixNumber(layerState, 'pos_y_max', 0);
                state.velocityDistX += (velocityTargetX - state.velocityDistX) * 0.5;
                state.velocityDistY += (velocityTargetY - state.velocityDistY) * 0.5;
                state.lastMouseX = rawX;
                state.lastMouseY = rawY;
                velocity = { mouseDeltaX, mouseDeltaY, distanceX, distanceY, distanceLength };
            }

            let desiredX = 0;
            let desiredY = 0;
            let followRotation = 0;
            let followScaleX = 1;
            let followScaleY = 1;
            const follow = dragSpeed > 0
                ? Math.max(0, Math.min(1, 1 / dragSpeed))
                : Math.max(0, Math.min(1, mouseDelay * targetFollowMultiplier * dt * 60));

            if (this.remixPositionFollowsMouse(layerState)) {
                if (followsMouseVelocity) {
                    desiredX = state.velocityDistX;
                    desiredY = state.velocityDistY;
                    if (this.remixBool(layerState, 'snap_pos')) {
                        if (Math.abs(velocity.distanceX) > 0.5) {
                            state.targetX += (desiredX - state.targetX) * follow;
                        }
                        if (Math.abs(velocity.distanceY) > 0.5) {
                            state.targetY += (desiredY - state.targetY) * follow;
                        }
                    } else {
                        state.targetX += (desiredX - state.targetX) * follow;
                        state.targetY += (desiredY - state.targetY) * follow;
                    }
                } else {
                    const pointerX = this.remixBool(layerState, 'pos_invert_x') ? -pointer.x : pointer.x;
                    const pointerY = this.remixBool(layerState, 'pos_invert_y') ? -pointer.y : pointer.y;
                    const sourceX = this.remixBool(layerState, 'pos_swap_x') ? pointerY : pointerX;
                    const sourceY = this.remixBool(layerState, 'pos_swap_y') ? pointerX : pointerY;
                    desiredX = sourceX < 0
                        ? Math.abs(sourceX) * this.remixNumber(layerState, 'pos_x_min', 0)
                        : sourceX * this.remixNumber(layerState, 'pos_x_max', 0);
                    desiredY = sourceY < 0
                        ? Math.abs(sourceY) * this.remixNumber(layerState, 'pos_y_min', 0)
                        : sourceY * this.remixNumber(layerState, 'pos_y_max', 0);
                    state.targetX += (desiredX - state.targetX) * follow;
                    state.targetY += (desiredY - state.targetY) * follow;
                }
            } else {
                state.targetX += (0 - state.targetX) * follow;
                state.targetY += (0 - state.targetY) * follow;
            }

            if (this.remixRotationFollowsMouse(layerState)) {
                if (followsMouseVelocity) {
                    const velocityDirection = -velocity.mouseDeltaX;
                    if (Math.abs(velocityDirection) > 0.0001) {
                        const minRot = this.remixNumber(layerState, 'rot_min', 0);
                        const maxRot = this.remixNumber(layerState, 'rot_max', 0);
                        const limitDeg = velocityDirection >= 0 ? maxRot : minRot;
                        followRotation = Math.abs(limitDeg) * Math.sign(limitDeg) * Math.PI / 180;
                    }
                } else {
                    const rotationPointer = this.remixBool(layerState, 'rot_invert_x') ? -pointer.x : pointer.x;
                    const minRot = this.remixNumber(layerState, 'rot_min', 0);
                    const maxRot = this.remixNumber(layerState, 'rot_max', 0);
                    const limitDeg = rotationPointer >= 0 ? maxRot : minRot;
                    const targetDeg = Math.abs(rotationPointer) * Math.abs(limitDeg) * Math.sign(limitDeg);
                    followRotation = targetDeg * Math.PI / 180;
                }
            }

            if (this.remixScaleFollowsMouse(layerState)) {
                if (followsMouseVelocity && Math.abs(velocity.mouseDeltaX) + Math.abs(velocity.mouseDeltaY) > 0.0001) {
                    const len = Math.max(0.0001, Math.hypot(velocity.mouseDeltaX, velocity.mouseDeltaY));
                    const sourceX = (velocity.mouseDeltaX / len) / 2;
                    const sourceY = (velocity.mouseDeltaY / len) / 2;
                    const limitX = sourceX >= 0
                        ? this.remixNumber(layerState, 'scale_x_max', 0)
                        : this.remixNumber(layerState, 'scale_x_min', 0);
                    const limitY = sourceY >= 0
                        ? this.remixNumber(layerState, 'scale_y_max', 0)
                        : this.remixNumber(layerState, 'scale_y_min', 0);
                    followScaleX = 1 + Math.abs(sourceX) * limitX;
                    followScaleY = 1 + Math.abs(sourceY) * limitY;
                } else if (!followsMouseVelocity) {
                    const scalePointerX = this.remixBool(layerState, 'scale_invert_x') ? -pointer.x : pointer.x;
                    const scalePointerY = this.remixBool(layerState, 'scale_invert_y') ? -pointer.y : pointer.y;
                    const sourceX = this.remixBool(layerState, 'scale_swap_x') ? scalePointerY : scalePointerX;
                    const sourceY = this.remixBool(layerState, 'scale_swap_y') ? scalePointerX : scalePointerY;
                    const limitX = sourceX >= 0
                        ? this.remixNumber(layerState, 'scale_x_max', 0)
                        : this.remixNumber(layerState, 'scale_x_min', 0);
                    const limitY = sourceY >= 0
                        ? this.remixNumber(layerState, 'scale_y_max', 0)
                        : this.remixNumber(layerState, 'scale_y_min', 0);
                    followScaleX = 1 + Math.abs(sourceX) * limitX;
                    followScaleY = 1 + Math.abs(sourceY) * limitY;
                }
            }

            let targetX = this.remixValue(layerState, 'animate_to_mouse_track_pos', true) === false ? 0 : state.targetX;
            let targetY = this.remixValue(layerState, 'animate_to_mouse_track_pos', true) === false ? 0 : state.targetY;
            targetX -= drag.x * dragSpeed * 0.011;
            targetY -= drag.y * dragSpeed * 0.011;
            if (this.layeredRuntimeFeatureEnabled('physics_v2') && this.remixBool(layerState, 'drag_snap')) {
                const pointerActive = !!this.layeredPointer?.active;
                if (!pointerActive && Math.hypot(drag.x, drag.y) < 1) {
                    targetX = 0;
                    targetY = 0;
                }
            }
            const previousX = state.x;
            const previousY = state.y;
            state.x += (targetX - state.x) * follow;
            state.y += (targetY - state.y) * follow;

            const depth = this.layeredPhysicsDepth(layer, layerState);
            const depthFactor = Math.min(2.4, 1 + depth * 0.18);
            const dragLength = ((targetX - previousX) + (targetY - previousY)) * depthFactor;
            const stretchVelocity = dragLength * stretchAmount * 0.01 * (physEff / 200);
            const targetStretch = Math.max(-0.35, Math.min(0.35, stretchVelocity));
            const physicsV2 = this.layeredRuntimeFeatureEnabled('physics_v2');
            const minLimit = this.remixNumber(layerState, physicsV2 ? 'chain_rot_min' : 'rLimitMin', this.remixNumber(layerState, 'rLimitMin', -180)) * Math.PI / 180;
            const maxLimit = this.remixNumber(layerState, physicsV2 ? 'chain_rot_max' : 'rLimitMax', this.remixNumber(layerState, 'rLimitMax', 180)) * Math.PI / 180;
            const chainSoftness = physicsV2
                ? Math.max(0.08, Math.min(1, this.remixNumber(layerState, 'chain_softness', 75) / 100))
                : 1;
            const dragRotationDegrees = dragLength * rotationStrength * (physEff / 200);
            const clampedDragRotation = Math.max(Math.min(minLimit, maxLimit), Math.min(Math.max(minLimit, maxLimit), dragRotationDegrees * Math.PI / 180));
            const targetRotation = followRotation + clampedDragRotation * chainSoftness;

            state.rotation += (targetRotation - state.rotation) * 0.15;
            state.stretch += (targetStretch - state.stretch) * 0.15;
            state.scaleX += (followScaleX - state.scaleX) * Math.max(0, Math.min(1, mouseDelay * dt * 60));
            state.scaleY += (followScaleY - state.scaleY) * Math.max(0, Math.min(1, mouseDelay * dt * 60));
            const animatedFrame = this.updateAnimateToMouseFrame(layerState, frame, pointer, state);

            return {
                x: state.x,
                y: state.y,
                rotation: state.rotation,
                scaleX: Math.max(0.1, state.scaleX * (1 - state.stretch)),
                scaleY: Math.max(0.1, state.scaleY * (1 + state.stretch)),
                meshX: state.x * (this.remixNumber(layerState, 'mesh_phys_x', 0) / 100),
                meshY: state.y * (this.remixNumber(layerState, 'mesh_phys_y', 0) / 100),
                frame: animatedFrame
            };
        }

        plusLayerPhysicsTransform(layer, layerState, timestamp = performance.now()) {
            if (!this.plusStateHasPhysics(layerState)) {
                return { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, meshX: 0, meshY: 0, frame: null };
            }
            const key = `plus:${this.layeredPhysicsKey(layer)}`;
            let state = this.layeredPhysicsByLayer.get(key);
            if (!state) {
                state = {
                    draggerX: 0,
                    draggerY: 0,
                    previousDraggerY: 0,
                    rotation: 0,
                    scaleX: 1,
                    scaleY: 1,
                    lastTime: timestamp
                };
                this.layeredPhysicsByLayer.set(key, state);
            }

            const tick = Math.max(0, (timestamp - (this.layeredAnimationStart || timestamp)) / 1000 * 60);
            const wobbleX = Math.sin(tick * (Number(layerState.xFrq) || 0)) * (Number(layerState.xAmp) || 0);
            const wobbleY = Math.sin(tick * (Number(layerState.yFrq) || 0)) * (Number(layerState.yAmp) || 0);
            const dragSpeed = Math.max(0, Number(layerState.dragSpeed) || 0);
            const follow = dragSpeed > 0 ? Math.max(0, Math.min(1, 1 / dragSpeed)) : 1;
            state.previousDraggerY = state.draggerY;
            state.draggerX += (wobbleX - state.draggerX) * follow;
            state.draggerY += (wobbleY - state.draggerY) * follow;

            const length = state.previousDraggerY - state.draggerY;
            const minLimit = Number(layerState.rLimitMin);
            const maxLimit = Number(layerState.rLimitMax);
            const minDeg = Number.isFinite(minLimit) ? minLimit : -180;
            const maxDeg = Number.isFinite(maxLimit) ? maxLimit : 180;
            const rawRot = length * (Number(layerState.rdragStr) || 0);
            const clampedRot = Math.max(Math.min(minDeg, maxDeg), Math.min(Math.max(minDeg, maxDeg), rawRot));
            const targetRotation = clampedRot * Math.PI / 180;
            let deltaRotation = targetRotation - state.rotation;
            while (deltaRotation > Math.PI) deltaRotation -= Math.PI * 2;
            while (deltaRotation < -Math.PI) deltaRotation += Math.PI * 2;
            state.rotation += deltaRotation * 0.25;

            const yvel = Math.max(-0.35, Math.min(0.35, length * (Number(layerState.stretchAmount) || 0) * 0.01));
            state.scaleX += ((1 - yvel) - state.scaleX) * 0.5;
            state.scaleY += ((1 + yvel) - state.scaleY) * 0.5;

            return {
                x: state.draggerX,
                y: state.draggerY,
                rotation: state.rotation,
                scaleX: Math.max(0.1, state.scaleX),
                scaleY: Math.max(0.1, state.scaleY),
                meshX: 0,
                meshY: 0,
                frame: null
            };
        }

        currentLayerMesh(layer, layerState) {
            if (layerState && layerState.mesh && typeof layerState.mesh === 'object') return layerState.mesh;
            if (layer && layer.mesh && typeof layer.mesh === 'object') return layer.mesh;
            return null;
        }

        layerMeshRuntimeEnabled(layer, layerState) {
            const mesh = this.currentLayerMesh(layer, layerState);
            return this.layeredRuntimeFeatureEnabled('mesh_deformation')
                && !!mesh
                && mesh.valid === true
                && Array.isArray(mesh.vertices)
                && Array.isArray(mesh.uvs)
                && Array.isArray(mesh.triangles)
                && mesh.vertices.length >= 3
                && mesh.uvs.length === mesh.vertices.length
                && mesh.triangles.length > 0;
        }

        meshPointToFrame(point, frame) {
            const x = Number(point?.[0]);
            const y = Number(point?.[1]);
            if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
            const normalized = Math.abs(x) <= 1.5 && Math.abs(y) <= 1.5;
            return {
                x: (normalized ? x * frame.dw : x) - frame.dw / 2,
                y: (normalized ? y * frame.dh : y) - frame.dh / 2
            };
        }

        meshUvToSource(point, frame) {
            const x = Number(point?.[0]);
            const y = Number(point?.[1]);
            if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
            const normalized = Math.abs(x) <= 1.5 && Math.abs(y) <= 1.5;
            return {
                x: frame.sx + (normalized ? x * frame.sw : x),
                y: frame.sy + (normalized ? y * frame.sh : y)
            };
        }

        meshTipPoint(layerState, frame) {
            const rawTip = this.remixValue(layerState, 'tip_point', null);
            const point = Array.isArray(rawTip) ? rawTip : [0.5, 0.5];
            const converted = this.meshPointToFrame(point, frame);
            return converted || { x: 0, y: -frame.dh / 2 };
        }

        deformedMeshDestinations(mesh, layerState, frame, physics) {
            const tip = this.meshTipPoint(layerState, frame);
            const maxDistance = Math.max(1, Math.hypot(frame.dw, frame.dh));
            const meshX = Number(physics?.meshX) || 0;
            const meshY = Number(physics?.meshY) || 0;
            const rotation = (Number(physics?.rotation) || 0) * 0.35;
            return mesh.vertices.map((point) => {
                const base = this.meshPointToFrame(point, frame);
                if (!base) return null;
                const dx = base.x - tip.x;
                const dy = base.y - tip.y;
                const influence = Math.max(0, Math.min(1, Math.hypot(dx, dy) / maxDistance));
                const angle = rotation * influence;
                const cos = Math.cos(angle);
                const sin = Math.sin(angle);
                return {
                    x: tip.x + dx * cos - dy * sin + meshX * influence * REMIX_MESH_DEFORM_STRENGTH,
                    y: tip.y + dx * sin + dy * cos + meshY * influence * REMIX_MESH_DEFORM_STRENGTH
                };
            });
        }

        drawAffineMeshTriangle(ctx, img, source, dest) {
            const denom = source[0].x * (source[1].y - source[2].y)
                + source[1].x * (source[2].y - source[0].y)
                + source[2].x * (source[0].y - source[1].y);
            if (Math.abs(denom) < 0.00001) return false;
            const a = (dest[0].x * (source[1].y - source[2].y)
                + dest[1].x * (source[2].y - source[0].y)
                + dest[2].x * (source[0].y - source[1].y)) / denom;
            const b = (dest[0].y * (source[1].y - source[2].y)
                + dest[1].y * (source[2].y - source[0].y)
                + dest[2].y * (source[0].y - source[1].y)) / denom;
            const c = (dest[0].x * (source[2].x - source[1].x)
                + dest[1].x * (source[0].x - source[2].x)
                + dest[2].x * (source[1].x - source[0].x)) / denom;
            const d = (dest[0].y * (source[2].x - source[1].x)
                + dest[1].y * (source[0].x - source[2].x)
                + dest[2].y * (source[1].x - source[0].x)) / denom;
            const e = (dest[0].x * (source[1].x * source[2].y - source[2].x * source[1].y)
                + dest[1].x * (source[2].x * source[0].y - source[0].x * source[2].y)
                + dest[2].x * (source[0].x * source[1].y - source[1].x * source[0].y)) / denom;
            const f = (dest[0].y * (source[1].x * source[2].y - source[2].x * source[1].y)
                + dest[1].y * (source[2].x * source[0].y - source[0].x * source[2].y)
                + dest[2].y * (source[0].x * source[1].y - source[1].x * source[0].y)) / denom;

            ctx.save();
            ctx.beginPath();
            ctx.moveTo(dest[0].x, dest[0].y);
            ctx.lineTo(dest[1].x, dest[1].y);
            ctx.lineTo(dest[2].x, dest[2].y);
            ctx.closePath();
            ctx.clip();
            ctx.transform(a, b, c, d, e, f);
            ctx.drawImage(img, 0, 0);
            ctx.restore();
            return true;
        }

        drawLayerMesh(ctx, img, layer, layerState, frame, physics) {
            const mesh = this.currentLayerMesh(layer, layerState);
            if (!mesh || !this.layerMeshRuntimeEnabled(layer, layerState)) return false;
            const destinations = this.deformedMeshDestinations(mesh, layerState, frame, physics);
            let drawn = 0;
            for (const triangle of mesh.triangles) {
                if (!Array.isArray(triangle) || triangle.length < 3) continue;
                const i0 = Number(triangle[0]);
                const i1 = Number(triangle[1]);
                const i2 = Number(triangle[2]);
                const dest = [destinations[i0], destinations[i1], destinations[i2]];
                const source = [
                    this.meshUvToSource(mesh.uvs[i0], frame),
                    this.meshUvToSource(mesh.uvs[i1], frame),
                    this.meshUvToSource(mesh.uvs[i2], frame)
                ];
                if (dest.some((point) => !point) || source.some((point) => !point)) continue;
                if (this.drawAffineMeshTriangle(ctx, img, source, dest)) drawn += 1;
            }
            return drawn > 0;
        }

        plusLayerSort(a, b) {
            const aState = this.layerStateForCurrentIndex(a);
            const bState = this.layerStateForCurrentIndex(b);
            return (Number(aState.z_index ?? a.zindex ?? 0) - Number(bState.z_index ?? b.zindex ?? 0))
                || (Number(a.order || 0) - Number(b.order || 0));
        }

        plusLayerParentId(layer) {
            const parentId = layer?.parent_id ?? layer?.parentId;
            return parentId === undefined || parentId === null || String(parentId).trim() === ''
                ? ''
                : String(parentId);
        }

        plusLayerTree(layers, stateName) {
            const allIds = new Set();
            layers.forEach((layer) => this.layerIdentityKeys(layer).forEach((id) => allIds.add(id)));
            const childrenByParent = new Map();
            const roots = [];
            layers
                .filter((layer) => this.shouldRenderLayer(layer, stateName))
                .forEach((layer) => {
                    const parentId = this.plusLayerParentId(layer);
                    if (parentId && allIds.has(parentId)) {
                        if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
                        childrenByParent.get(parentId).push(layer);
                    } else {
                        roots.push(layer);
                    }
                });
            roots.sort((a, b) => this.plusLayerSort(a, b));
            childrenByParent.forEach((items) => items.sort((a, b) => this.plusLayerSort(a, b)));
            return { roots, childrenByParent };
        }

        plusVector(value, fallbackX = 0, fallbackY = 0) {
            if (!Array.isArray(value) || value.length < 2) return [fallbackX, fallbackY];
            const x = Number(value[0]);
            const y = Number(value[1]);
            return [Number.isFinite(x) ? x : fallbackX, Number.isFinite(y) ? y : fallbackY];
        }

        plusLayerTransform(layer, layerState, drawFrame, isRoot = false) {
            const fallbackX = (Number(layerState.x ?? layer.x) || 0) + drawFrame.dw / 2;
            const fallbackY = (Number(layerState.y ?? layer.y) || 0) + drawFrame.dh / 2;
            const node = isRoot
                ? this.plusVector(layerState.node_origin || layer.node_origin, fallbackX, fallbackY)
                : this.plusVector(layerState.local_position || layer.local_position || layerState.position, 0, 0);
            const drawOffset = this.plusVector(
                layerState.draw_offset || layer.draw_offset,
                -drawFrame.dw / 2,
                -drawFrame.dh / 2
            );
            return {
                nodeX: node[0],
                nodeY: node[1],
                drawX: drawOffset[0] + drawFrame.legacyOffsetX,
                drawY: drawOffset[1] + drawFrame.legacyOffsetY
            };
        }

        drawPlusLayerTree(ctx, layers, stateName, timestamp = performance.now()) {
            const modelMotion = this.modelMotionTransform(stateName, timestamp);
            const canvasPadding = Number(this.layeredCanvasPadding) || 0;
            const tree = this.plusLayerTree(layers, stateName);
            tree.roots.forEach((layer) => {
                this.drawPlusLayerNode(ctx, layer, tree.childrenByParent, stateName, timestamp, modelMotion, canvasPadding, true, new Set());
            });
            return true;
        }

        drawPlusLayerNode(ctx, layer, childrenByParent, stateName, timestamp, modelMotion, canvasPadding, isRoot, visited) {
            const layerId = this.primaryLayerId(layer);
            if (!layerId || visited.has(layerId) || visited.size > 64) return;
            visited.add(layerId);
            const img = this.layeredImages.get(layer._imageIndex);
            const children = childrenByParent.get(layerId) || [];
            if (!img) {
                children.forEach((child) => this.drawPlusLayerNode(ctx, child, childrenByParent, stateName, timestamp, modelMotion, canvasPadding, false, visited));
                visited.delete(layerId);
                return;
            }
            const layerState = this.layerStateForCurrentIndex(layer);
            const frame = this.stateFrameInfo(layer, layerState, img, timestamp);
            const physics = this.plusLayerPhysicsTransform(layer, layerState, timestamp);
            const drawFrame = physics.frame === null || physics.frame === undefined
                ? frame
                : this.stateFrameInfo(layer, layerState, img, timestamp, physics.frame);
            const transform = this.plusLayerTransform(layer, layerState, drawFrame, isRoot);
            const layerModelMotion = isRoot && this.remixIgnoresModelBounce(layerState) ? { x: 0, y: 0 } : modelMotion;
            const rootX = isRoot ? canvasPadding + layerModelMotion.x : 0;
            const rootY = isRoot ? canvasPadding + layerModelMotion.y : 0;
            const rawScale = Array.isArray(layerState.scale) ? layerState.scale : [1, 1];
            const baseScale = Array.isArray(layer.base_scale) ? layer.base_scale : [1, 1];
            const baseScaleX = Number(baseScale[0]) || 1;
            const baseScaleY = Number(baseScale[1]) || 1;
            const relativeFlipX = !!layerState.flip_sprite_h !== !!layer.base_flip_h;
            const relativeFlipY = !!layerState.flip_sprite_v !== !!layer.base_flip_v;
            const scaleX = ((Number(rawScale[0]) || 1) / baseScaleX) * (relativeFlipX ? -1 : 1) * physics.scaleX;
            const scaleY = ((Number(rawScale[1]) || 1) / baseScaleY) * (relativeFlipY ? -1 : 1) * physics.scaleY;
            const rotation = (Number(layerState.rotation) || 0) + physics.rotation;

            ctx.save();
            ctx.translate(rootX + transform.nodeX + physics.x, rootY + transform.nodeY + physics.y);
            if (rotation) ctx.rotate(rotation);
            ctx.scale(scaleX, scaleY);
            ctx.drawImage(
                img,
                drawFrame.sx,
                drawFrame.sy,
                drawFrame.sw,
                drawFrame.sh,
                transform.drawX,
                transform.drawY,
                drawFrame.dw,
                drawFrame.dh
            );
            if (this.layeredRuntimeFeatureEnabled('clip_children_rect') && !!layerState.clipped) {
                ctx.save();
                ctx.beginPath();
                ctx.rect(transform.drawX, transform.drawY, drawFrame.dw, drawFrame.dh);
                ctx.clip();
                children.forEach((child) => this.drawPlusLayerNode(ctx, child, childrenByParent, stateName, timestamp, modelMotion, canvasPadding, false, visited));
                ctx.restore();
            } else {
                children.forEach((child) => this.drawPlusLayerNode(ctx, child, childrenByParent, stateName, timestamp, modelMotion, canvasPadding, false, visited));
            }
            ctx.restore();
            visited.delete(layerId);
        }

        layerDrawZIndex(layer, layerState = null) {
            layerState = layerState || this.layerStateForCurrentIndex(layer);
            const raw = layerState.effective_z_index ?? layer.effective_zindex;
            const value = Number(raw);
            if (Number.isFinite(value)) return value;
            return this.fallbackLayerDrawZIndex(layer, layerState);
        }

        layerLocalZIndex(layer, layerState = null) {
            layerState = layerState || this.layerStateForCurrentIndex(layer);
            const value = Number(layerState.z_index ?? layer.zindex ?? 0);
            return Number.isFinite(value) ? value : 0;
        }

        fallbackLayerDrawZIndex(layer, layerState = null) {
            const layers = Array.isArray(this.layeredMetadata?.layers) ? this.layeredMetadata.layers : null;
            if (this._fallbackLayersBySpriteIdSource !== layers) {
                this._fallbackLayersBySpriteId = new Map();
                this._fallbackLayersBySpriteIdSource = layers;
                (layers || []).forEach((candidate) => {
                    if (candidate && candidate.sprite_id !== undefined && candidate.sprite_id !== null) {
                        this._fallbackLayersBySpriteId.set(String(candidate.sprite_id), candidate);
                    }
                });
            }
            const layersBySpriteId = this._fallbackLayersBySpriteId;
            let total = 0;
            let current = layer;
            let currentState = layerState || this.layerStateForCurrentIndex(current);
            const visited = new Set();
            while (current) {
                const spriteId = current.sprite_id;
                const visitKey = spriteId !== undefined && spriteId !== null ? String(spriteId) : `order:${current.order}`;
                if (visited.has(visitKey)) break;
                visited.add(visitKey);
                total += this.layerLocalZIndex(current, currentState);
                const zAsRelative = currentState.z_as_relative ?? current.z_as_relative;
                if (zAsRelative === false) break;
                const parentId = current.parent_id;
                if (parentId === undefined || parentId === null) break;
                current = layersBySpriteId.get(String(parentId));
                currentState = current ? this.layerStateForCurrentIndex(current) : null;
            }
            return total;
        }

        compareLayerDrawOrder(a, b) {
            const aState = this.layerStateForCurrentIndex(a);
            const bState = this.layerStateForCurrentIndex(b);
            return (this.layerDrawZIndex(a, aState) - this.layerDrawZIndex(b, bState))
                || (Number(a.order || 0) - Number(b.order || 0));
        }

        drawLayeredState(stateName = this.state || 'idle', timestamp = performance.now()) {
            if (!this.isLayeredActive() || !this.canvasElement) return false;
            const canvas = this.canvasElement;
            const ctx = canvas.getContext('2d');
            if (!ctx) return false;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const layers = Array.isArray(this.layeredMetadata.layers) ? this.layeredMetadata.layers : [];
            if (this.isLayeredPlusModel()) {
                return this.drawPlusLayerTree(ctx, layers, stateName, timestamp);
            }
            const modelMotion = this.modelMotionTransform(stateName, timestamp);
            const layerMotionEnabled = this.layeredRuntimeFeatureEnabled('layer_motion');
            const canvasPadding = Number(this.layeredCanvasPadding) || 0;
            layers
                .filter((layer) => this.shouldRenderLayer(layer, stateName))
                .sort((a, b) => this.compareLayerDrawOrder(a, b))
                .forEach((layer) => {
                    const img = this.layeredImages.get(layer._imageIndex);
                    if (!img) return;
                    const layerState = this.layerStateForCurrentIndex(layer);
                    const frame = this.stateFrameInfo(layer, layerState, img, timestamp);
                    const physics = this.layeredPhysicsTransform(layer, layerState, timestamp, frame);
                    const drawFrame = physics.frame === null || physics.frame === undefined
                        ? frame
                        : this.stateFrameInfo(layer, layerState, img, timestamp, physics.frame);
                    const baseX = (Number(layerState.x ?? layer.x) || 0) + drawFrame.legacyOffsetX + canvasPadding;
                    const baseY = (Number(layerState.y ?? layer.y) || 0) + drawFrame.legacyOffsetY + canvasPadding;
                    const layerModelMotion = this.remixIgnoresModelBounce(layerState) ? { x: 0, y: 0 } : modelMotion;
                    const x = baseX
                        + layerModelMotion.x
                        + (layerMotionEnabled ? this.motionValue(layerState.xAmp, layerState.xFrq, timestamp, Number(layer.order || 0) * 0.17) : 0)
                        + physics.x;
                    const y = baseY
                        + layerModelMotion.y
                        + (layerMotionEnabled ? this.motionValue(layerState.yAmp, layerState.yFrq, timestamp, Number(layer.order || 0) * 0.23) : 0)
                        + physics.y;
                    const wiggleDegrees = layerMotionEnabled
                        ? this.motionValue(layerState.wiggle_amp, layerState.wiggle_freq || layerState.rot_frq, timestamp, Number(layer.order || 0) * 0.11)
                        : 0;
                    const remixRotation = this.remixLayerOscillationRotation(layerState, timestamp, Number(layer.order || 0) * 0.13);
                    const rotation = (Number(layerState.rotation) || 0) + wiggleDegrees * Math.PI / 180 + remixRotation + physics.rotation;
                    const rawScale = Array.isArray(layerState.scale) ? layerState.scale : [1, 1];
                    const baseScale = Array.isArray(layer.base_scale) ? layer.base_scale : [1, 1];
                    const baseScaleX = Number(baseScale[0]) || 1;
                    const baseScaleY = Number(baseScale[1]) || 1;
                    const relativeFlipX = !!layerState.flip_sprite_h !== !!layer.base_flip_h;
                    const relativeFlipY = !!layerState.flip_sprite_v !== !!layer.base_flip_v;
                    const scaleX = ((Number(rawScale[0]) || 1) / baseScaleX) * (relativeFlipX ? -1 : 1) * physics.scaleX;
                    const scaleY = ((Number(rawScale[1]) || 1) / baseScaleY) * (relativeFlipY ? -1 : 1) * physics.scaleY;
                    ctx.save();
                    ctx.translate(x + drawFrame.dw / 2, y + drawFrame.dh / 2);
                    if (rotation) ctx.rotate(rotation);
                    ctx.scale(scaleX, scaleY);
                    const drewMesh = this.drawLayerMesh(ctx, img, layer, layerState, drawFrame, physics);
                    if (!drewMesh) {
                        ctx.drawImage(
                            img,
                            drawFrame.sx,
                            drawFrame.sy,
                            drawFrame.sw,
                            drawFrame.sh,
                            -drawFrame.dw / 2,
                            -drawFrame.dh / 2,
                            drawFrame.dw,
                            drawFrame.dh
                        );
                    }
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

        applyTransform(timestamp = performance.now()) {
            if (!this.image) return;
            const bounce = this.currentSpeakingBounceTransform();
            const breathing = this.currentLayeredBreathingTransform(timestamp);
            const talkingHop = this.currentTalkingHopTransform(timestamp);
            const placement = this.getActivePlacement();
            const renderPlacement = this.getRenderPlacement(placement);
            const scaleX = this.config.mirror ? -renderPlacement.scale : renderPlacement.scale;
            const finalScaleX = scaleX * bounce.scaleX * breathing.scaleX * talkingHop.scaleX;
            const finalScaleY = renderPlacement.scale * bounce.scaleY * breathing.scaleY * talkingHop.scaleY;
            const modelManagerPage = isModelManagerPage();
            const pointerEvents = this.isLocked ? 'none' : 'auto';
            if (this.container) {
                this.container.style.pointerEvents = 'none';
            }
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
            this.image.style.transform = `${anchorTranslate} translate(${renderPlacement.offsetX}px, ${renderPlacement.offsetY + bounce.y + breathing.y + talkingHop.y}px) scale(${finalScaleX}, ${finalScaleY})`;
        }

        getActiveLayoutFields() {
            return isPngtuberMobileWebPage()
                ? { scale: 'mobile_scale', offsetX: 'mobile_offset_x', offsetY: 'mobile_offset_y' }
                : { scale: 'scale', offsetX: 'offset_x', offsetY: 'offset_y' };
        }

        readConfigNumber(key, fallback) {
            const value = Number(this.config[key]);
            return Number.isFinite(value) ? value : fallback;
        }

        getActivePlacement() {
            const fields = this.getActiveLayoutFields();
            const desktopScale = this.readConfigNumber('scale', 1);
            const scaleFallback = fields.scale === 'mobile_scale' ? Math.min(desktopScale, 1) : 1;
            return {
                fields,
                scale: clampNumber(this.config[fields.scale], SCALE_MIN, SCALE_MAX, scaleFallback),
                offsetX: this.readConfigNumber(fields.offsetX, 0),
                offsetY: this.readConfigNumber(fields.offsetY, 0)
            };
        }

        getRenderPlacement(placement) {
            if (isModelManagerPage() && !this.config.preserve_model_manager_position) {
                return Object.assign({}, placement, {
                    offsetX: 0,
                    offsetY: 0
                });
            }
            return placement;
        }

        setActiveScale(nextScale) {
            const placement = this.getActivePlacement();
            this.config[placement.fields.scale] = clampNumber(nextScale, SCALE_MIN, SCALE_MAX, placement.scale);
        }

        setActiveOffsets(offsetX, offsetY) {
            const fields = this.getActiveLayoutFields();
            this.config[fields.offsetX] = Math.max(-5000, Math.min(5000, offsetX));
            this.config[fields.offsetY] = Math.max(-5000, Math.min(5000, offsetY));
        }

        applyScale(nextScale) {
            this.setActiveScale(nextScale);
            this.applyTransform();
            this.syncGlobalConfig();
            if (typeof this.updateFloatingButtonsPosition === 'function') {
                this.updateFloatingButtonsPosition();
            }
            this.updateLockIconPosition();
        }

        syncGlobalConfig() {
            if (isModelManagerPage()) return;
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
                const shouldHideButtons = this.isLocked
                    || isYuiGuideFloatingToolbarSuppressed()
                    || this._pngtuberFloatingControlsVisible === false;
                this._floatingButtonsContainer.style.display = shouldHideButtons ? 'none' : 'flex';
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
            const placement = this.getActivePlacement();
            this._dragState = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                startOffsetX: placement.offsetX,
                startOffsetY: placement.offsetY,
                lastX: event.clientX,
                lastY: event.clientY,
                lastAt: performance.now(),
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
            const now = performance.now();
            this.updateLayeredDragVelocity(event.clientX - state.lastX, event.clientY - state.lastY, now);
            state.lastX = event.clientX;
            state.lastY = event.clientY;
            state.lastAt = now;
            if (Math.hypot(dx, dy) > 4 && !state.moved) {
                state.moved = true;
                this.showDragImage();
            }
            this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);
            this.applyTransform();
            if (this.isLayeredActive()) this.drawLayeredState();
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
            this.restartLayeredAnimationLoop();
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
            const currentScale = this.getActivePlacement().scale;
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
            const placement = this.getActivePlacement();
            this._dragState = null;
            this._touchZoomState = {
                initialDistance: this.getTouchDistance(event.touches[0], event.touches[1]),
                initialScale: placement.scale,
                startCenterX: center.x,
                startCenterY: center.y,
                startOffsetX: placement.offsetX,
                startOffsetY: placement.offsetY,
                lastCenterX: center.x,
                lastCenterY: center.y,
                lastAt: performance.now(),
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
            const now = performance.now();
            this.updateLayeredDragVelocity(center.x - state.lastCenterX, center.y - state.lastCenterY, now);
            state.lastCenterX = center.x;
            state.lastCenterY = center.y;
            state.lastAt = now;
            state.changed = Math.abs(scaleChange - 1) > 0.01 || Math.hypot(dx, dy) > 4;
            this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);
            this.applyScale(state.initialScale * scaleChange);
            if (this.isLayeredActive()) this.drawLayeredState();
        }

        async endTouchZoom() {
            const state = this._touchZoomState;
            if (!state) return;
            this._touchZoomState = null;
            document.body.classList.remove('neko-model-dragging');
            if (this.image) this.image.classList.remove('is-dragging');
            this.restoreStateImage();
            this.restartLayeredAnimationLoop();
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
            if (isYuiGuideFloatingToolbarSuppressed()) {
                lockIcon.style.display = 'none';
                lockIcon.style.visibility = 'hidden';
                lockIcon.style.opacity = '0';
                return;
            }
            const image = this.image || (this.ensureContainer() && this.image);
            const rect = image ? image.getBoundingClientRect() : null;
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                if (!window.isInTutorial) lockIcon.style.display = 'none';
                return;
            }
            if (this._pngtuberFloatingControlsVisible === false) {
                lockIcon.style.display = 'none';
                lockIcon.style.visibility = 'hidden';
                lockIcon.style.opacity = '0';
                return;
            }
            const lockGap = 28;
            const lockVerticalGap = 80;
            const targetX = rect.right * 0.7 + rect.left * 0.3 + lockGap;
            const targetY = rect.top * 0.3 + rect.bottom * 0.7 + lockVerticalGap;
            const defaultMaxTop = window.innerHeight - 40;
            const maxTop = typeof window.getNekoYuiGuideLockIconMaxTop === 'function'
                ? window.getNekoYuiGuideLockIconMaxTop(defaultMaxTop, 40)
                : defaultMaxTop;
            lockIcon.style.left = `${Math.max(0, Math.min(targetX, window.innerWidth - 40))}px`;
            lockIcon.style.top = `${Math.max(0, Math.min(targetY, maxTop))}px`;
            lockIcon.style.display = 'block';
            lockIcon.style.visibility = 'visible';

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
            if (isModelManagerPage()) return false;
            if ((window.lanlan_config?.model_type || '').toLowerCase() !== 'pngtuber') {
                return false;
            }
            const saveKey = [
                this.config.offset_x,
                this.config.offset_y,
                this.config.scale,
                this.config.mobile_offset_x,
                this.config.mobile_offset_y,
                this.config.mobile_scale,
                this.config.mirror
            ].join(':');
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
            this.clearEmotion({ render: false });
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
            if (state === 'talking') {
                if (!this.hasIndependentTalkingImage() && this.emotionImage) return this.emotionImage;
                return this.config.talking_image || this.emotionImage || this.config.idle_image || DEFAULT_PLACEHOLDER;
            }
            if (state === 'idle') return this.emotionImage || this.config.idle_image || DEFAULT_PLACEHOLDER;
            const emotionKey = `${state}_image`;
            return this.config[emotionKey] || this.config.idle_image || DEFAULT_PLACEHOLDER;
        }

        normalizeEmotionName(emotion) {
            return String(emotion || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
        }

        emotionImageFor(emotion) {
            const key = EMOTION_IMAGE_KEYS[this.normalizeEmotionName(emotion)];
            return key ? this.config[key] || '' : '';
        }

        hasIndependentTalkingImage() {
            const talking = this.config.talking_image || '';
            return !!(talking && talking !== this.config.idle_image);
        }

        clearEmotion(options = {}) {
            if (this.emotionTimer) {
                clearTimeout(this.emotionTimer);
                this.emotionTimer = null;
            }
            this.currentEmotion = null;
            this.emotionImage = '';
            if (options.render !== false && this.isLayeredActive()) {
                this.setLayeredStateIndex(0, { source: 'emotion-clear' });
            } else if (options.render !== false) {
                const nextState = this.isSpeaking && this.speakingMouthOpen && this.hasIndependentTalkingImage()
                    ? 'talking'
                    : 'idle';
                this.setState(nextState, { restartLayeredAnimation: false });
            }
            return true;
        }

        setLayeredEmotion(emotionName, options = {}) {
            if (!this.isLayeredActive()) return false;
            const stateIndex = this.layeredEmotionTarget(emotionName);
            if (stateIndex === null) return false;
            if (this.emotionTimer) {
                clearTimeout(this.emotionTimer);
                this.emotionTimer = null;
            }
            this.currentEmotion = emotionName;
            this.emotionImage = '';
            const durationMs = Number(options.durationMs);
            const shouldAutoClear = options.durationMs === undefined
                ? true
                : Number.isFinite(durationMs) && durationMs > 0;
            if (shouldAutoClear) {
                this.emotionTimer = setTimeout(() => {
                    this.emotionTimer = null;
                    this.clearEmotion();
                }, options.durationMs === undefined ? DEFAULT_EMOTION_DURATION_MS : durationMs);
            }
            return this.setLayeredStateIndex(stateIndex, { source: 'emotion' });
        }

        setEmotion(emotion, options = {}) {
            options = options && typeof options === 'object' ? options : {};
            const emotionName = this.normalizeEmotionName(emotion);
            if (CLEAR_EMOTIONS.has(emotionName)) {
                return this.clearEmotion();
            }
            if (this.isLayeredActive()) return this.setLayeredEmotion(emotionName, options);
            const nextImage = this.emotionImageFor(emotionName);
            if (!nextImage) return false;

            if (this.emotionTimer) {
                clearTimeout(this.emotionTimer);
                this.emotionTimer = null;
            }
            this.currentEmotion = emotionName;
            this.emotionImage = nextImage;
            const durationMs = Number(options.durationMs);
            const shouldAutoClear = options.durationMs === undefined
                ? true
                : Number.isFinite(durationMs) && durationMs > 0;
            if (shouldAutoClear) {
                this.emotionTimer = setTimeout(() => {
                    this.emotionTimer = null;
                    this.clearEmotion();
                }, options.durationMs === undefined ? DEFAULT_EMOTION_DURATION_MS : durationMs);
            }
            const nextState = this.isSpeaking && this.speakingMouthOpen && this.hasIndependentTalkingImage()
                ? 'talking'
                : 'idle';
            this.setState(nextState, { restartLayeredAnimation: false });
            return true;
        }

        setState(state, options = {}) {
            this.state = state || 'idle';
            this.ensureContainer();
            if (this.isLayeredActive()) {
                this.drawLayeredState(this.state);
                if (options.restartLayeredAnimation !== false) {
                    this.restartLayeredAnimationLoop();
                } else if (!this.layeredAnimationFrame && this.hasMotionLayersForCurrentState()) {
                    this.startLayeredAnimationLoop({ preserveTimeline: true });
                }
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
            if (this.isLayeredRemixModel()) return null;
            if (this.isLayeredActive()) return null;
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
            if (this._renderingPaused) return;
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
            const tick = (timestamp = performance.now()) => {
                const progress = (timestamp - this.speakingBounceStart) / this.speakingBounceDuration;
                if (progress >= 1 || !this.container || this.container.style.display === 'none') {
                    this.speakingBounceFrame = null;
                    this.speakingBounceStart = 0;
                    this.applyTransform();
                    return;
                }
                this.applyAnimationTransform(timestamp);
                this.updateOverlayPositionsForAnimation(timestamp);
                this.speakingBounceFrame = requestAnimationFrame(tick);
            };
            this.speakingBounceFrame = requestAnimationFrame(tick);
        }

        currentTalkingHopTransform(timestamp = performance.now()) {
            if (!this.talkingHopStart || !this.talkingHopAmplitude || !this.talkingHopPeriodMs) {
                return { y: 0, scaleX: 1, scaleY: 1 };
            }
            const elapsed = Math.max(0, timestamp - this.talkingHopStart);
            const progress = (elapsed % this.talkingHopPeriodMs) / this.talkingHopPeriodMs;
            const wave = Math.sin(progress * Math.PI);
            return {
                y: -this.talkingHopAmplitude * wave,
                scaleX: 1,
                scaleY: 1 + 0.004 * wave
            };
        }

        startTalkingHopAnimation() {
            if (this._renderingPaused) return;
            if (this.talkingHopFrame || !this.isSpeaking || !this.isLayeredActive()) return;
            this.talkingHopStart = performance.now();
            this.talkingHopAmplitude = 4.5;
            this.talkingHopPeriodMs = 260;
            const tick = (timestamp = performance.now()) => {
                if (!this.isSpeaking || !this.container || this.container.style.display === 'none') {
                    this.stopTalkingHopAnimation();
                    return;
                }
                this.applyAnimationTransform(timestamp);
                this.updateOverlayPositionsForAnimation(timestamp);
                this.talkingHopFrame = requestAnimationFrame(tick);
            };
            this.talkingHopFrame = requestAnimationFrame(tick);
        }

        startCostumeChangeHopAnimation() {
            if (this.talkingHopFrame || !this.isLayeredActive()) return;
            this.talkingHopStart = performance.now();
            this.talkingHopAmplitude = 4.5;
            this.talkingHopPeriodMs = 260;
            const tick = (timestamp = performance.now()) => {
                const elapsed = timestamp - this.talkingHopStart;
                if (elapsed >= this.talkingHopPeriodMs || !this.container || this.container.style.display === 'none') {
                    this.stopTalkingHopAnimation();
                    return;
                }
                this.applyAnimationTransform(timestamp);
                this.updateOverlayPositionsForAnimation(timestamp);
                this.talkingHopFrame = requestAnimationFrame(tick);
            };
            this.talkingHopFrame = requestAnimationFrame(tick);
        }

        stopTalkingHopAnimation() {
            if (this.talkingHopFrame) {
                cancelAnimationFrame(this.talkingHopFrame);
                this.talkingHopFrame = null;
            }
            this.talkingHopStart = 0;
            this.talkingHopAmplitude = 0;
            this.talkingHopPeriodMs = 0;
            this.applyTransform();
        }

        applyLipSyncMouthState(open) {
            if (this.lipSyncMouthState === open && this.speakingMouthOpen === open) return;
            this.lipSyncMouthState = open;
            this.speakingMouthOpen = open;
            if (open) {
                this.startSpeakingBounceAnimation();
            }
            this.setState(open ? 'talking' : 'idle', { restartLayeredAnimation: false });
        }

        startLipSync(analyser) {
            if (this._renderingPaused) return false;
            if (!analyser || typeof analyser.getByteTimeDomainData !== 'function') {
                this.startSpeakingMouthAnimation();
                return false;
            }
            if (this.lipSyncFrame) {
                cancelAnimationFrame(this.lipSyncFrame);
                this.lipSyncFrame = null;
            }
            if (this.speakingMouthTimer) {
                clearTimeout(this.speakingMouthTimer);
                this.speakingMouthTimer = null;
            }
            this.isSpeaking = true;
            this.startTalkingHopAnimation();
            this.lipSyncMouthOpen = 0;
            this.lipSyncMouthState = !!this.speakingMouthOpen;
            this.lipSyncLastStateChangeAt = performance.now();
            this.lipSyncNextPulseAt = this.lipSyncLastStateChangeAt;
            this.lipSyncPulseCloseAt = 0;
            const sampleSize = Math.max(32, Number(analyser.fftSize) || 2048);
            const dataArray = new Uint8Array(sampleSize);
            const tick = (timestamp = performance.now()) => {
                if (!this.isSpeaking || !analyser || typeof analyser.getByteTimeDomainData !== 'function') {
                    this.stopLipSync();
                    return;
                }
                analyser.getByteTimeDomainData(dataArray);
                let sum = 0;
                for (let i = 0; i < dataArray.length; i += 1) {
                    const value = (dataArray[i] - 128) / 128;
                    sum += value * value;
                }
                const rms = Math.sqrt(sum / dataArray.length);
                const targetOpen = Math.min(1, rms * 10);
                this.lipSyncMouthOpen = this.lipSyncMouthOpen * 0.55 + targetOpen * 0.45;
                const activeThreshold = 0.16;
                const quietThreshold = 0.07;
                const pulseOpenMs = Math.max(42, Math.min(72, 42 + this.lipSyncMouthOpen * 34));
                const pulseGapMs = Math.max(45, Math.min(135, 135 - this.lipSyncMouthOpen * 90));
                if (this.lipSyncMouthState && timestamp >= this.lipSyncPulseCloseAt) {
                    this.applyLipSyncMouthState(false);
                    this.lipSyncNextPulseAt = timestamp + pulseGapMs;
                } else if (!this.lipSyncMouthState && this.lipSyncMouthOpen >= activeThreshold && timestamp >= this.lipSyncNextPulseAt) {
                    this.applyLipSyncMouthState(true);
                    this.lipSyncPulseCloseAt = timestamp + pulseOpenMs;
                } else if (this.lipSyncMouthState && this.lipSyncMouthOpen <= quietThreshold) {
                    this.applyLipSyncMouthState(false);
                    this.lipSyncNextPulseAt = timestamp + pulseGapMs;
                }
                this.lipSyncFrame = requestAnimationFrame(tick);
            };
            this.lipSyncFrame = requestAnimationFrame(tick);
            return true;
        }

        stopLipSync() {
            if (this.lipSyncFrame) {
                cancelAnimationFrame(this.lipSyncFrame);
                this.lipSyncFrame = null;
            }
            this.lipSyncMouthOpen = 0;
            this.lipSyncMouthState = false;
            this.lipSyncLastStateChangeAt = 0;
            this.lipSyncNextPulseAt = 0;
            this.lipSyncPulseCloseAt = 0;
            this.stopTalkingHopAnimation();
            if (this.speakingMouthOpen) {
                this.speakingMouthOpen = false;
                this.setState('idle', { restartLayeredAnimation: false });
            }
        }

        scheduleSpeakingMouthFrame() {
            if (this._renderingPaused) return;
            if (!this.isSpeaking) return;
            if (this.lipSyncFrame) return;
            const nextDelay = this.speakingMouthOpen
                ? 80 + Math.random() * 90
                : 55 + Math.random() * 95;
            this.speakingMouthTimer = setTimeout(() => {
                this.speakingMouthTimer = null;
                if (!this.isSpeaking || this.lipSyncFrame) return;
                this.speakingMouthOpen = !this.speakingMouthOpen;
                if (this.speakingMouthOpen) {
                    this.startSpeakingBounceAnimation();
                }
                this.setState(this.speakingMouthOpen ? 'talking' : 'idle', { restartLayeredAnimation: false });
                this.scheduleSpeakingMouthFrame();
            }, nextDelay);
        }

        startSpeakingMouthAnimation() {
            if (this._renderingPaused) return;
            this.isSpeaking = true;
            this.startTalkingHopAnimation();
            if (this.lipSyncFrame) return;
            if (this.speakingMouthTimer) return;
            this.speakingMouthOpen = true;
            this.startSpeakingBounceAnimation();
            this.setState('talking', { restartLayeredAnimation: false });
            this.scheduleSpeakingMouthFrame();
        }

        stopSpeakingMouthAnimation() {
            this.isSpeaking = false;
            this.speakingMouthOpen = false;
            if (this.speakingMouthTimer) {
                clearTimeout(this.speakingMouthTimer);
                this.speakingMouthTimer = null;
            }
            this.stopLipSync();
            this.stopTalkingHopAnimation();
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
                .sort((a, b) => this.compareLayerDrawOrder(a, b))
                .map((layer) => {
                    const layerState = this.layerStateForCurrentIndex(layer);
                    const img = this.layeredImages.get(layer._imageIndex);
                    const frame = img ? this.stateFrameInfo(layer, layerState, img) : null;
                    const mesh = this.currentLayerMesh(layer, layerState);
                    const meshRuntime = this.layerMeshRuntimeEnabled(layer, layerState);
                    const physicsRuntime = this.stateHasRemixPhysics(layerState);
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
                        open_eyes: (layerState.effective_open_eyes ?? layerState.open_eyes) !== false,
                        meshMetadata: !!mesh,
                        meshRuntime,
                        meshReason: meshRuntime ? '' : (mesh?.degrade_reason || (mesh ? 'mesh runtime feature disabled' : 'mesh metadata absent')),
                        physicsRuntime,
                        physicsVersion: this.layeredRuntimeFeatureEnabled('physics_v2') ? 2 : 1
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
            const metadataCapabilities = Object.assign({}, this.layeredMetadata?.capabilities || {});
            const runtimeFeatures = Object.assign({}, this.layeredMetadata?.runtime_features || {});
            const unsupportedFeatures = Array.isArray(runtimeFeatures.unsupported_features)
                ? runtimeFeatures.unsupported_features.slice()
                : [];
            const enabledRuntimeFeatures = Object.keys(runtimeFeatures)
                .filter((key) => key !== 'unsupported_features' && runtimeFeatures[key] === true);
            const meshLayers = layers.filter((layer) => {
                const state = this.layerStateForCurrentIndex(layer);
                return this.layerMeshRuntimeEnabled(layer, state);
            }).length;
            const emotionSupported = this.isLayeredActive()
                ? this.layeredEmotionSupported()
                : Object.keys(EMOTION_IMAGE_KEYS).some((emotion) => !!this.emotionImageFor(emotion));
            const imageRect = image && typeof image.getBoundingClientRect === 'function'
                ? image.getBoundingClientRect()
                : null;
            return {
                active: !!(container && container.style.display !== 'none' && !container.classList.contains('hidden')),
                modelType: (window.lanlan_config?.model_type || '').toLowerCase() || null,
                state: this.state,
                currentEmotion: this.currentEmotion,
                emotionImage: this.emotionImage || null,
                emotionSupported,
                emotionTimer: !!this.emotionTimer,
                isSpeaking: !!this.isSpeaking,
                speakingMouthOpen: !!this.speakingMouthOpen,
                layered: this.isLayeredActive(),
                layeredConfigured: this.isLayeredConfigured(),
                layeredStateIndex: this.layeredStateIndex,
                layeredToggles: Object.fromEntries(this.layeredToggleVisibility || new Map()),
                sourceFormat: this.layeredMetadata?.source_format || this.config.source_format || null,
                adapterVersion: Number(this.layeredMetadata?.adapter_version || 1),
                metadataCapabilities,
                runtimeFeatures,
                enabledRuntimeFeatures,
                unsupportedFeatures,
                meshMetadata: metadataCapabilities.mesh_metadata === true || metadataCapabilities.mesh === true,
                meshRuntime: metadataCapabilities.mesh_runtime === true || runtimeFeatures.mesh_deformation === true,
                meshLayers,
                physicsVersion: runtimeFeatures.physics_v2 === true ? 2 : 1,
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
                    lipSyncFrame: !!this.lipSyncFrame,
                    talkingHopFrame: !!this.talkingHopFrame,
                    emotionTimer: !!this.emotionTimer,
                    blinkTimer: !!this.layeredBlinkTimer,
                    blinkEndTimer: !!this.layeredBlinkEndTimer,
                    returnIdleTimer: !!this.returnIdleTimer,
                    layeredAnimationFrame: !!this.layeredAnimationFrame,
                    layeredBreathingFrame: !!this.layeredBreathingFrame
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
            if (this._renderingPaused) {
                this.isSpeaking = !!isSpeaking;
                return;
            }
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
            this.container.style.pointerEvents = 'none';
            if (this.image) {
                this.image.style.visibility = 'visible';
                this.image.style.pointerEvents = this.isLocked ? 'none' : 'auto';
                this.applyTransform();
            }
            if (this.isLayeredActive()) {
                this.drawLayeredState();
                if (!this.layeredBlinkTimer && !this.layeredBlinkEndTimer) {
                    this.startLayeredBlinkLoop();
                }
                this.restartLayeredAnimationLoop();
                this.attachLayeredHotkeys();
                this.attachLayeredPlayEvent();
                this.attachLayeredPointerTracking();
            }
        }

        hide() {
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            this.detachLayeredPlayEvent();
            this.detachLayeredPointerTracking();
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
            if (this._pngtuberHideButtonsTimer) {
                clearTimeout(this._pngtuberHideButtonsTimer);
                this._pngtuberHideButtonsTimer = null;
            }
            if (this._pngtuberPointerEvaluateFrame) {
                cancelAnimationFrame(this._pngtuberPointerEvaluateFrame);
                this._pngtuberPointerEvaluateFrame = null;
            }
            if (typeof this.cleanupFloatingButtons === 'function') {
                this.cleanupFloatingButtons();
            }
            this._lockIconElement = null;
            this._lockIconImages = null;
            this.clearLayeredTimers();
            this.detachLayeredHotkeys();
            this.detachLayeredPlayEvent();
            this.detachLayeredPointerTracking();
            this.layeredMetadata = null;
            this.layeredImages = new Map();
            this.layeredToggleVisibility = new Map();
            this.layeredLayerById = new Map();
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
                    if (typeof this.setMouseTrackingEnabled === 'function') {
                        this.setMouseTrackingEnabled(enabled);
                    } else {
                        window.mouseTrackingEnabled = enabled;
                    }
                },
                getMouseTrackingState: function() {
                    return typeof this.isMouseTrackingEnabled === 'function'
                        ? this.isMouseTrackingEnabled()
                        : window.mouseTrackingEnabled !== false;
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

    function isYuiGuideFloatingToolbarSuppressed() {
        return !!(
            window.isNekoYuiGuideFloatingToolbarSuppressed
            && window.isNekoYuiGuideFloatingToolbarSuppressed()
        );
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
            if (this._pngtuberHideButtonsTimer) {
                clearTimeout(this._pngtuberHideButtonsTimer);
                this._pngtuberHideButtonsTimer = null;
            }
            if (this._pngtuberPointerEvaluateFrame) {
                cancelAnimationFrame(this._pngtuberPointerEvaluateFrame);
                this._pngtuberPointerEvaluateFrame = null;
            }
            this._pngtuberFloatingControlsVisible = true;
            this._pngtuberControlsHover = false;

            this.updateFloatingButtonsPosition = () => {
                if (isYuiGuideFloatingToolbarSuppressed()) {
                    buttonsContainer.style.display = 'none';
                    buttonsContainer.style.visibility = 'hidden';
                    buttonsContainer.style.opacity = '0';
                    this.updateLockIconPosition();
                    return;
                }
                if (this._isInReturnState) {
                    buttonsContainer.style.display = 'none';
                    return;
                }
                if (this.isLocked) {
                    buttonsContainer.style.display = 'none';
                    this.updateLockIconPosition();
                    return;
                }
                if (this._pngtuberFloatingControlsVisible === false) {
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
            const pointInRect = (x, y, rect, expand = 0) => {
                if (!rect || !Number.isFinite(x) || !Number.isFinite(y)) return false;
                return x >= rect.left - expand && x <= rect.right + expand
                    && y >= rect.top - expand && y <= rect.bottom + expand;
            };
            const getImageRect = () => {
                const image = this.image || (this.ensureContainer() && this.image);
                if (!image) return null;
                const rect = image.getBoundingClientRect();
                if (!rect || rect.width <= 0 || rect.height <= 0) return null;
                return rect;
            };
            const hasOpenPngtuberOverlay = () => {
                const popupUi = window.AvatarPopupUI || null;
                if (popupUi && typeof popupUi.hasVisibleOverlay === 'function' && popupUi.hasVisibleOverlay('pngtuber')) {
                    return true;
                }
                return Array.from(document.querySelectorAll('[id^="pngtuber-popup-"], [data-neko-sidepanel]')).some((el) => {
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : el.style;
                    return style && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0;
                });
            };
            const shouldKeepFloatingControlsVisible = () => {
                if (this._pngtuberControlsHover || hasOpenPngtuberOverlay()) return true;
                const x = this._lastPngtuberPointerX;
                const y = this._lastPngtuberPointerY;
                if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
                const imageRect = getImageRect();
                if (pointInRect(x, y, imageRect, 24)) return true;
                const lockIcon = this._lockIconElement || document.getElementById('pngtuber-lock-icon');
                if (lockIcon && lockIcon.style.display !== 'none' && pointInRect(x, y, lockIcon.getBoundingClientRect(), 8)) return true;
                if (buttonsContainer && buttonsContainer.style.display !== 'none' && pointInRect(x, y, buttonsContainer.getBoundingClientRect(), 8)) return true;
                return false;
            };
            const clearHideTimer = () => {
                if (this._pngtuberHideButtonsTimer) {
                    clearTimeout(this._pngtuberHideButtonsTimer);
                    this._pngtuberHideButtonsTimer = null;
                }
            };
            const hideFloatingControls = () => {
                this._pngtuberFloatingControlsVisible = false;
                buttonsContainer.style.display = 'none';
                const lockIcon = this._lockIconElement || document.getElementById('pngtuber-lock-icon');
                if (lockIcon) {
                    lockIcon.style.display = 'none';
                    lockIcon.style.visibility = 'hidden';
                    lockIcon.style.opacity = '0';
                }
            };
            const showFloatingControls = () => {
                this._pngtuberFloatingControlsVisible = true;
                clearHideTimer();
                applyResponsiveFloatingLayout();
                this.updateLockIconPosition();
            };
            const startHideTimer = (delay = 1000) => {
                if (window.isInTutorial === true) return;
                if (this._pngtuberHideButtonsTimer) return;
                this._pngtuberHideButtonsTimer = setTimeout(() => {
                    this._pngtuberHideButtonsTimer = null;
                    if (window.isInTutorial === true || shouldKeepFloatingControlsVisible()) {
                        startHideTimer(delay);
                        return;
                    }
                    hideFloatingControls();
                }, delay);
            };
            const markControlsHover = () => {
                this._pngtuberControlsHover = true;
                showFloatingControls();
            };
            const unmarkControlsHover = () => {
                this._pngtuberControlsHover = false;
                startHideTimer();
            };
            const evaluatePointerForFloatingControls = () => {
                if (shouldKeepFloatingControlsVisible()) {
                    showFloatingControls();
                } else {
                    startHideTimer();
                }
            };
            const schedulePointerEvaluation = () => {
                if (this._pngtuberPointerEvaluateFrame) return;
                this._pngtuberPointerEvaluateFrame = requestAnimationFrame(() => {
                    this._pngtuberPointerEvaluateFrame = null;
                    evaluatePointerForFloatingControls();
                });
            };
            const bindLockHoverHandlers = () => {
                const lockIcon = this._lockIconElement || document.getElementById('pngtuber-lock-icon');
                if (!lockIcon || lockIcon._pngtuberFloatingAutoHideBound) return;
                lockIcon._pngtuberFloatingAutoHideBound = true;
                lockIcon.addEventListener('mouseenter', markControlsHover);
                lockIcon.addEventListener('mouseleave', unmarkControlsHover);
            };
            const handlePointerMove = (event) => {
                this._lastPngtuberPointerX = event.clientX;
                this._lastPngtuberPointerY = event.clientY;
                schedulePointerEvaluation();
            };
            const handleImagePointerEnter = () => showFloatingControls();
            const handleImagePointerLeave = () => startHideTimer();
            const clearPointerAndHideSoon = () => {
                this._lastPngtuberPointerX = null;
                this._lastPngtuberPointerY = null;
                this._pngtuberControlsHover = false;
                startHideTimer(250);
            };
            const handleWindowFocus = () => {
                if (shouldKeepFloatingControlsVisible()) {
                    showFloatingControls();
                }
            };
            const handleWindowBlur = () => clearPointerAndHideSoon();
            const handleDocumentMouseEnter = (event) => {
                if (event && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
                    handlePointerMove(event);
                    return;
                }
                if (shouldKeepFloatingControlsVisible()) {
                    showFloatingControls();
                }
            };
            const handleDocumentMouseLeave = () => clearPointerAndHideSoon();

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
                this.applyTransform();
                applyResponsiveFloatingLayout();
                this.updateLockIconPosition();
            });
            this._uiWindowHandlers.push({ event: 'resize', handler: scheduleLayout, target: window });
            this._uiWindowHandlers.push({ event: 'orientationchange', handler: scheduleLayout, target: window });
            this._uiWindowHandlers.push({ event: 'neko:yui-guide-floating-toolbar-suppression-change', handler: scheduleLayout, target: window });
            window.addEventListener('resize', scheduleLayout);
            window.addEventListener('orientationchange', scheduleLayout);
            window.addEventListener('neko:yui-guide-floating-toolbar-suppression-change', scheduleLayout);
            if (this.image) {
                this.image.addEventListener('load', scheduleLayout);
                this.image.addEventListener('pointerenter', handleImagePointerEnter);
                this.image.addEventListener('pointerleave', handleImagePointerLeave);
                this.image.addEventListener('mouseover', handleImagePointerEnter);
                this._uiWindowHandlers.push({ event: 'load', handler: scheduleLayout, target: this.image });
                this._uiWindowHandlers.push({ event: 'pointerenter', handler: handleImagePointerEnter, target: this.image });
                this._uiWindowHandlers.push({ event: 'pointerleave', handler: handleImagePointerLeave, target: this.image });
                this._uiWindowHandlers.push({ event: 'mouseover', handler: handleImagePointerEnter, target: this.image });
            }
            buttonsContainer.addEventListener('mouseenter', markControlsHover);
            buttonsContainer.addEventListener('mouseleave', unmarkControlsHover);
            window.addEventListener('pointermove', handlePointerMove, { passive: true });
            window.addEventListener('focus', handleWindowFocus);
            window.addEventListener('blur', handleWindowBlur);
            document.addEventListener('mouseenter', handleDocumentMouseEnter, true);
            document.addEventListener('mouseleave', handleDocumentMouseLeave, true);
            this._uiWindowHandlers.push({ event: 'pointermove', handler: handlePointerMove, target: window, options: { passive: true } });
            this._uiWindowHandlers.push({ event: 'focus', handler: handleWindowFocus, target: window });
            this._uiWindowHandlers.push({ event: 'blur', handler: handleWindowBlur, target: window });
            this._uiWindowHandlers.push({ event: 'mouseenter', handler: handleDocumentMouseEnter, target: document, options: true });
            this._uiWindowHandlers.push({ event: 'mouseleave', handler: handleDocumentMouseLeave, target: document, options: true });
            bindLockHoverHandlers();
            setTimeout(bindLockHoverHandlers, 0);

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
