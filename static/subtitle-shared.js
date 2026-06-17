(function() {
    'use strict';

    if (window.nekoSubtitleShared) {
        return;
    }

    var SETTINGS_EVENT = 'neko-subtitle-settings-change';
    var RENDER_EVENT = 'neko-subtitle-render-state';
    var DEFAULT_BACKGROUND_OPACITY = 95;
    var DEFAULT_PANEL_BOUNDS = { width: 600, height: 68 };
    var MIN_PANEL_WIDTH = 48;
    var MIN_PANEL_HEIGHT = 28;
    var DEFAULT_TRANSLATION_LANGUAGE = 'zh';
    var DEFAULT_UI_LOCALE = 'zh-CN';
    var CONTROLS_HIDE_DELAY_MS = 1200;
    var PANEL_TEXT_HORIZONTAL_RESERVE = 110;
    var SETTINGS_KEYS = {
        subtitleEnabled: 'subtitleEnabled',
        userLanguage: 'userLanguage',
        subtitleOpacity: 'subtitleOpacity',
        subtitlePanelBounds: 'subtitlePanelBounds',
        subtitlePanelPosition: 'subtitlePanelPosition',
        subtitlePanelLocked: 'subtitlePanelLocked',
        subtitleInteractionPassthrough: 'subtitleInteractionPassthrough'
    };
    var UI_KEY_MAP = {
        settingsBtn: 'subtitle.settings.settingsBtn',
        lockPosition: 'subtitle.settings.lockPosition',
        unlockPosition: 'subtitle.settings.unlockPosition',
        closePanel: 'subtitle.settings.closePanel',
        targetLang: 'subtitle.settings.targetLang',
        opacity: 'subtitle.settings.opacity',
        passthroughInteraction: 'subtitle.settings.passthroughInteraction',
        emptyHint: 'subtitle.display.emptyHint'
    };
    var LOCK_ICON_PATH = 'M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v9a1 1 0 01-1 1H6a1 1 0 01-1-1v-9a1 1 0 011-1h1zm2 0h6V7a3 3 0 00-6 0v3z';
    var UNLOCK_ICON_PATH = 'M12 17a2 2 0 100-4 2 2 0 000 4zm6-7h-8V7a3 3 0 015.64-1.44 1 1 0 001.73-1A5 5 0 008 7v3H6a1 1 0 00-1 1v9a1 1 0 001 1h12a1 1 0 001-1v-9a1 1 0 00-1-1z';
    var UI_FALLBACK = {
        'zh-CN': {
            settingsBtn: '字幕设置',
            lockPosition: '锁定位置',
            unlockPosition: '解锁位置',
            closePanel: '关闭翻译面板',
            targetLang: '目标语言',
            opacity: '背景不透明度',
            passthroughInteraction: '透明区域穿透',
            emptyHint: '暂无翻译内容'
        },
        'zh-TW': {
            settingsBtn: '字幕設定',
            lockPosition: '鎖定位置',
            unlockPosition: '解鎖位置',
            closePanel: '關閉翻譯面板',
            targetLang: '目標語言',
            opacity: '背景不透明度',
            passthroughInteraction: '透明區域穿透',
            emptyHint: '暫無翻譯內容'
        },
        en: {
            settingsBtn: 'Subtitle Settings',
            lockPosition: 'Lock position',
            unlockPosition: 'Unlock position',
            closePanel: 'Close translation panel',
            targetLang: 'Target Language',
            opacity: 'Background opacity',
            passthroughInteraction: 'Transparent area passthrough',
            emptyHint: 'No translation yet'
        },
        es: {
            settingsBtn: 'Configuración de subtítulos',
            lockPosition: 'Bloquear posición',
            unlockPosition: 'Desbloquear posición',
            closePanel: 'Cerrar panel de traducción',
            targetLang: 'Idioma de destino',
            opacity: 'Opacidad del fondo',
            passthroughInteraction: 'Clics en área transparente',
            emptyHint: 'Sin traducción todavía'
        },
        pt: {
            settingsBtn: 'Configurações de legenda',
            lockPosition: 'Bloquear posição',
            unlockPosition: 'Desbloquear posição',
            closePanel: 'Fechar painel de tradução',
            targetLang: 'Idioma de destino',
            opacity: 'Opacidade do fundo',
            passthroughInteraction: 'Clique através da área transparente',
            emptyHint: 'Sem tradução ainda'
        },
        ja: {
            settingsBtn: '字幕設定',
            lockPosition: '位置をロック',
            unlockPosition: '位置ロックを解除',
            closePanel: '翻訳パネルを閉じる',
            targetLang: '翻訳先の言語',
            opacity: '背景の不透明度',
            passthroughInteraction: '透明領域をクリック透過',
            emptyHint: '翻訳はまだありません'
        },
        ko: {
            settingsBtn: '자막 설정',
            lockPosition: '위치 잠금',
            unlockPosition: '위치 잠금 해제',
            closePanel: '번역 패널 닫기',
            targetLang: '대상 언어',
            opacity: '배경 불투명도',
            passthroughInteraction: '투명 영역 클릭 통과',
            emptyHint: '아직 번역이 없습니다'
        },
        ru: {
            settingsBtn: 'Настройки субтитров',
            lockPosition: 'Заблокировать положение',
            unlockPosition: 'Разблокировать положение',
            closePanel: 'Закрыть панель перевода',
            targetLang: 'Целевой язык',
            opacity: 'Непрозрачность фона',
            passthroughInteraction: 'Пропуск кликов в прозрачных областях',
            emptyHint: 'Перевода пока нет'
        }
    };

    var settingsState = null;
    var renderState = null;

    function clonePanelPosition(position) {
        if (!position) return null;
        return {
            left: position.left,
            top: position.top,
            coordinateSpace: position.coordinateSpace
        };
    }

    function clonePanelBounds(bounds) {
        if (!bounds) return null;
        return {
            width: bounds.width,
            height: bounds.height
        };
    }

    function clone(obj) {
        var next = Object.assign({}, obj);
        if (obj && hasOwn(obj, 'subtitlePanelPosition')) {
            next.subtitlePanelPosition = clonePanelPosition(obj.subtitlePanelPosition);
        }
        if (obj && hasOwn(obj, 'subtitlePanelBounds')) {
            next.subtitlePanelBounds = clonePanelBounds(obj.subtitlePanelBounds);
        }
        return next;
    }

    function hasOwn(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function normalizeTranslationLanguageCode(lang) {
        if (!lang) return DEFAULT_TRANSLATION_LANGUAGE;
        var value = String(lang).trim().toLowerCase();
        if (value.indexOf('ja') === 0) return 'ja';
        if (value.indexOf('en') === 0) return 'en';
        if (value.indexOf('ko') === 0) return 'ko';
        if (value.indexOf('ru') === 0) return 'ru';
        if (value.indexOf('es') === 0) return 'es';
        if (value.indexOf('pt') === 0) return 'pt';
        return 'zh';
    }

    function normalizeUiLocale(locale) {
        if (!locale) return DEFAULT_UI_LOCALE;
        var value = String(locale).trim();
        var lower = value.toLowerCase();
        if (lower.indexOf('zh') === 0) {
            if (/(tw|hk|hant)/i.test(value)) {
                return 'zh-TW';
            }
            return 'zh-CN';
        }
        if (lower.indexOf('ja') === 0) return 'ja';
        if (lower.indexOf('ko') === 0) return 'ko';
        if (lower.indexOf('ru') === 0) return 'ru';
        if (lower.indexOf('es') === 0) return 'es';
        if (lower.indexOf('pt') === 0) return 'pt';
        if (lower.indexOf('en') === 0) return 'en';
        return DEFAULT_UI_LOCALE;
    }

    function clampOpacity(value) {
        var number = parseInt(value, 10);
        if (!isFinite(number)) return DEFAULT_BACKGROUND_OPACITY;
        return Math.max(0, Math.min(100, number));
    }

    function formatAlpha(value) {
        return String(Math.round(value * 100) / 100);
    }

    function normalizePanelPosition(position) {
        var value = position;
        if (!value) return null;
        if (typeof value === 'string') {
            try {
                value = JSON.parse(value);
            } catch (_) {
                return null;
            }
        }
        if (!value || typeof value !== 'object') return null;
        var rawLeft = hasOwn(value, 'left') ? value.left : value.x;
        var rawTop = hasOwn(value, 'top') ? value.top : value.y;
        var left = Number(rawLeft);
        var top = Number(rawTop);
        if (!isFinite(left) || !isFinite(top)) return null;
        return {
            left: Math.max(0, left),
            top: Math.max(0, top),
            coordinateSpace: 'viewport'
        };
    }

    function samePanelPosition(a, b) {
        if (!a && !b) return true;
        if (!a || !b) return false;
        return a.left === b.left &&
            a.top === b.top &&
            a.coordinateSpace === b.coordinateSpace;
    }

    function normalizePanelBounds(bounds) {
        var value = bounds;
        if (!value) return null;
        if (typeof value === 'string') {
            try {
                value = JSON.parse(value);
            } catch (_) {
                return null;
            }
        }
        if (!value || typeof value !== 'object') return null;
        var rawWidth = hasOwn(value, 'width') ? value.width : value.w;
        var rawHeight = hasOwn(value, 'height') ? value.height : value.h;
        var width = Number(rawWidth);
        var height = Number(rawHeight);
        if (!isFinite(width) || !isFinite(height)) return null;
        return {
            width: Math.max(MIN_PANEL_WIDTH, Math.round(width)),
            height: Math.max(MIN_PANEL_HEIGHT, Math.round(height))
        };
    }

    function getPanelBounds(bounds) {
        return normalizePanelBounds(bounds) || clonePanelBounds(DEFAULT_PANEL_BOUNDS);
    }

    function samePanelBounds(a, b) {
        if (!a && !b) return true;
        if (!a || !b) return false;
        return a.width === b.width && a.height === b.height;
    }

    function normalizePanelState(state) {
        var value = String(state || '').trim().toLowerCase();
        if (value === 'controls' || value === 'settings') return value;
        return 'clean';
    }

    function getCurrentUiLocale() {
        var source = '';
        try {
            if (window.i18next && window.i18next.language) {
                source = window.i18next.language;
            } else if (window.localStorage) {
                source = localStorage.getItem('i18nextLng') || '';
            }
        } catch (_) {}
        if (!source && document && document.documentElement) {
            source = document.documentElement.lang || '';
        }
        if (!source && navigator) {
            source = navigator.language || navigator.userLanguage || '';
        }
        return normalizeUiLocale(source);
    }

    function ensureSettingsState() {
        if (settingsState) {
            return settingsState;
        }
        settingsState = {
            subtitleEnabled: false,
            userLanguage: DEFAULT_TRANSLATION_LANGUAGE,
            subtitleOpacity: DEFAULT_BACKGROUND_OPACITY,
            subtitlePanelBounds: clonePanelBounds(DEFAULT_PANEL_BOUNDS),
            subtitlePanelPosition: null,
            subtitlePanelLocked: false,
            subtitleInteractionPassthrough: true,
            uiLocale: getCurrentUiLocale()
        };
        try {
            settingsState.subtitleEnabled = localStorage.getItem(SETTINGS_KEYS.subtitleEnabled) === 'true';
            settingsState.userLanguage = normalizeTranslationLanguageCode(localStorage.getItem(SETTINGS_KEYS.userLanguage) || DEFAULT_TRANSLATION_LANGUAGE);
            settingsState.subtitleOpacity = clampOpacity(localStorage.getItem(SETTINGS_KEYS.subtitleOpacity));
            settingsState.subtitlePanelBounds = getPanelBounds(localStorage.getItem(SETTINGS_KEYS.subtitlePanelBounds));
            settingsState.subtitlePanelPosition = normalizePanelPosition(localStorage.getItem(SETTINGS_KEYS.subtitlePanelPosition));
            settingsState.subtitlePanelLocked = localStorage.getItem(SETTINGS_KEYS.subtitlePanelLocked) === 'true';
            settingsState.subtitleInteractionPassthrough = localStorage.getItem(SETTINGS_KEYS.subtitleInteractionPassthrough) !== 'false';
        } catch (_) {}
        return settingsState;
    }

    function ensureRenderState() {
        if (renderState) {
            return renderState;
        }
        var current = ensureSettingsState();
        renderState = {
            text: '',
            visible: false,
            subtitleEnabled: current.subtitleEnabled,
            userLanguage: current.userLanguage,
            uiLocale: current.uiLocale,
            subtitleOpacity: current.subtitleOpacity,
            subtitlePanelBounds: clonePanelBounds(current.subtitlePanelBounds),
            subtitlePanelPosition: clonePanelPosition(current.subtitlePanelPosition),
            subtitlePanelLocked: current.subtitlePanelLocked,
            subtitleInteractionPassthrough: current.subtitleInteractionPassthrough,
            subtitlePanelState: 'clean'
        };
        return renderState;
    }

    function writeSettingsToStorage(nextState, changedKeys) {
        try {
            if (changedKeys.indexOf('subtitleEnabled') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleEnabled, String(nextState.subtitleEnabled));
            }
            if (changedKeys.indexOf('userLanguage') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.userLanguage, nextState.userLanguage);
            }
            if (changedKeys.indexOf('subtitleOpacity') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleOpacity, String(nextState.subtitleOpacity));
            }
            if (changedKeys.indexOf('subtitlePanelBounds') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitlePanelBounds, JSON.stringify(nextState.subtitlePanelBounds));
            }
            if (changedKeys.indexOf('subtitlePanelPosition') !== -1) {
                if (nextState.subtitlePanelPosition) {
                    localStorage.setItem(SETTINGS_KEYS.subtitlePanelPosition, JSON.stringify(nextState.subtitlePanelPosition));
                } else {
                    localStorage.removeItem(SETTINGS_KEYS.subtitlePanelPosition);
                }
            }
            if (changedKeys.indexOf('subtitlePanelLocked') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitlePanelLocked, String(nextState.subtitlePanelLocked));
            }
            if (changedKeys.indexOf('subtitleInteractionPassthrough') !== -1) {
                localStorage.setItem(SETTINGS_KEYS.subtitleInteractionPassthrough, String(nextState.subtitleInteractionPassthrough));
            }
        } catch (_) {}
    }

    function syncAppState(nextState, changedKeys) {
        if (!window.appState) return;
        if (changedKeys.indexOf('subtitleEnabled') !== -1) {
            window.appState.subtitleEnabled = nextState.subtitleEnabled;
        }
        if (changedKeys.indexOf('userLanguage') !== -1) {
            window.appState.userLanguage = nextState.userLanguage;
        }
    }

    function dispatchSettingsChange(nextState, changedKeys, source) {
        window.dispatchEvent(new CustomEvent(SETTINGS_EVENT, {
            detail: {
                state: clone(nextState),
                changedKeys: changedKeys.slice(),
                source: source || ''
            }
        }));
    }

    function updateRenderState(patch, options) {
        var current = ensureRenderState();
        var next = clone(current);
        var changedKeys = [];
        var keys = [
            'text', 'visible', 'subtitleEnabled', 'userLanguage', 'uiLocale',
            'subtitleOpacity', 'subtitlePanelBounds',
            'subtitlePanelPosition', 'subtitlePanelLocked',
            'subtitleInteractionPassthrough', 'subtitlePanelState'
        ];
        var i;

        for (i = 0; i < keys.length; i++) {
            var key = keys[i];
            if (!hasOwn(patch, key)) continue;
            var value = patch[key];
            if (key === 'text') value = String(value || '');
            if (key === 'visible') value = !!value;
            if (key === 'subtitleEnabled') value = !!value;
            if (key === 'userLanguage') value = normalizeTranslationLanguageCode(value);
            if (key === 'uiLocale') value = normalizeUiLocale(value);
            if (key === 'subtitleOpacity') value = clampOpacity(value);
            if (key === 'subtitlePanelBounds') value = getPanelBounds(value);
            if (key === 'subtitlePanelPosition') value = normalizePanelPosition(value);
            if (key === 'subtitlePanelLocked') value = !!value;
            if (key === 'subtitleInteractionPassthrough') value = value !== false;
            if (key === 'subtitlePanelState') value = normalizePanelState(value);
            var changed = key === 'subtitlePanelPosition'
                ? !samePanelPosition(next[key], value)
                : (key === 'subtitlePanelBounds' ? !samePanelBounds(next[key], value) : next[key] !== value);
            if (changed) {
                next[key] = value;
                changedKeys.push(key);
            }
        }

        if (!changedKeys.length) {
            return clone(current);
        }

        renderState = next;
        window.dispatchEvent(new CustomEvent(RENDER_EVENT, {
            detail: {
                state: clone(next),
                changedKeys: changedKeys,
                source: options && options.source ? options.source : ''
            }
        }));
        return clone(next);
    }

    function updateSettings(patch, options) {
        var current = ensureSettingsState();
        var next = clone(current);
        var changedKeys = [];
        var uiLocale = hasOwn(patch, 'uiLocale')
            ? normalizeUiLocale(patch.uiLocale)
            : (options && options.refreshUiLocale ? getCurrentUiLocale() : current.uiLocale);

        if (hasOwn(patch, 'subtitleEnabled')) {
            next.subtitleEnabled = !!patch.subtitleEnabled;
        }
        if (hasOwn(patch, 'userLanguage')) {
            next.userLanguage = normalizeTranslationLanguageCode(patch.userLanguage);
        }
        if (hasOwn(patch, 'subtitleOpacity')) {
            next.subtitleOpacity = clampOpacity(patch.subtitleOpacity);
        }
        if (hasOwn(patch, 'subtitlePanelBounds')) {
            next.subtitlePanelBounds = getPanelBounds(patch.subtitlePanelBounds);
        }
        if (hasOwn(patch, 'subtitlePanelPosition')) {
            next.subtitlePanelPosition = normalizePanelPosition(patch.subtitlePanelPosition);
        }
        if (hasOwn(patch, 'subtitlePanelLocked')) {
            next.subtitlePanelLocked = !!patch.subtitlePanelLocked;
        }
        if (hasOwn(patch, 'subtitleInteractionPassthrough')) {
            next.subtitleInteractionPassthrough = patch.subtitleInteractionPassthrough !== false;
        }
        next.uiLocale = uiLocale;

        var keys = [
            'subtitleEnabled', 'userLanguage', 'subtitleOpacity',
            'subtitlePanelBounds', 'subtitlePanelPosition',
            'subtitlePanelLocked', 'subtitleInteractionPassthrough', 'uiLocale'
        ];
        for (var i = 0; i < keys.length; i++) {
            var key = keys[i];
            var changed = key === 'subtitlePanelPosition'
                ? !samePanelPosition(next[key], current[key])
                : (key === 'subtitlePanelBounds' ? !samePanelBounds(next[key], current[key]) : next[key] !== current[key]);
            if (changed) {
                changedKeys.push(key);
            }
        }

        if (!changedKeys.length) {
            return clone(current);
        }

        settingsState = next;
        if (!options || options.persist !== false) {
            writeSettingsToStorage(next, changedKeys);
        }
        syncAppState(next, changedKeys);
        updateRenderState({
            subtitleEnabled: next.subtitleEnabled,
            userLanguage: next.userLanguage,
            uiLocale: next.uiLocale,
            subtitleOpacity: next.subtitleOpacity,
            subtitlePanelBounds: next.subtitlePanelBounds,
            subtitlePanelPosition: next.subtitlePanelPosition,
            subtitlePanelLocked: next.subtitlePanelLocked,
            subtitleInteractionPassthrough: next.subtitleInteractionPassthrough
        }, { source: options && options.source ? options.source : 'subtitle-settings' });
        if (!options || options.silent !== true) {
            dispatchSettingsChange(next, changedKeys, options && options.source);
        }
        return clone(next);
    }

    function getSettings() {
        return clone(ensureSettingsState());
    }

    function getRenderState() {
        return clone(ensureRenderState());
    }

    function subscribeToWindowEvent(eventName, listener, immediateState, immediateDetail) {
        function handler(evt) {
            if (!evt || !evt.detail) return;
            listener(evt.detail.state, evt.detail);
        }
        window.addEventListener(eventName, handler);
        if (immediateState) {
            listener(immediateState, immediateDetail || { changedKeys: [], source: 'init' });
        }
        return function unsubscribe() {
            window.removeEventListener(eventName, handler);
        };
    }

    function subscribeSettings(listener, options) {
        return subscribeToWindowEvent(
            SETTINGS_EVENT,
            listener,
            options && options.immediate === false ? null : getSettings(),
            { changedKeys: [], source: 'init' }
        );
    }

    function subscribeRenderState(listener, options) {
        return subscribeToWindowEvent(
            RENDER_EVENT,
            listener,
            options && options.immediate === false ? null : getRenderState(),
            { changedKeys: [], source: 'init' }
        );
    }

    function getUiText(key, uiLocale) {
        var i18nKey = UI_KEY_MAP[key];
        if (i18nKey && typeof window.t === 'function') {
            try {
                var translated = window.t(i18nKey);
                if (translated && translated !== i18nKey) {
                    return translated;
                }
            } catch (_) {}
        }
        return getUiFallbackText(key, uiLocale);
    }

    function getUiFallbackText(key, uiLocale) {
        var locale = normalizeUiLocale(uiLocale || ensureSettingsState().uiLocale || getCurrentUiLocale());
        var dictionary = UI_FALLBACK[locale] || UI_FALLBACK[DEFAULT_UI_LOCALE];
        return dictionary[key] || UI_FALLBACK[DEFAULT_UI_LOCALE][key] || key;
    }

    function query(root, selector) {
        if (!root) return null;
        if (typeof root.querySelector === 'function') {
            return root.querySelector(selector);
        }
        if (root.document && typeof root.document.querySelector === 'function') {
            return root.document.querySelector(selector);
        }
        return null;
    }

    function getSubtitleRefs(root) {
        return {
            display: query(root, '#subtitle-display'),
            scroll: query(root, '#subtitle-scroll'),
            text: query(root, '#subtitle-text'),
            panelControls: query(root, '#subtitle-panel-controls'),
            lockBtn: query(root, '#subtitle-lock-btn'),
            settingsBtn: query(root, '#subtitle-settings-btn'),
            closeBtn: query(root, '#subtitle-close-btn'),
            settingsPanel: query(root, '#subtitle-settings-panel'),
            labels: root && typeof root.querySelectorAll === 'function' ? root.querySelectorAll('.subtitle-settings-label') : [],
            langSelect: query(root, '#subtitle-lang-select'),
            opacitySlider: query(root, '#subtitle-opacity-slider'),
            opacityValue: query(root, '#subtitle-opacity-value'),
            passthroughToggle: query(root, '#subtitle-passthrough-toggle'),
            resizeHandles: root && typeof root.querySelectorAll === 'function' ? root.querySelectorAll('.subtitle-resize-edge') : []
        };
    }

    function isDarkThemeActive() {
        return !!(
            document.documentElement &&
            document.documentElement.getAttribute('data-theme') === 'dark'
        );
    }

    function applyBackgroundOpacity(display, opacity) {
        if (!display) return;
        var opacityValue = clampOpacity(opacity);
        var alpha = opacityValue / 100;
        display.style.removeProperty('background');
        display.style.setProperty('--subtitle-panel-alpha', String(alpha));
        display.style.setProperty('--subtitle-panel-soft-alpha', formatAlpha(alpha));
        display.style.setProperty('--subtitle-panel-soft-mid-alpha', formatAlpha(alpha));
        display.style.setProperty('--subtitle-panel-soft-edge-alpha', formatAlpha(alpha));
        display.dataset.subtitleBackgroundOpacity = String(opacityValue);
    }

    function applySubtitlePanelBounds(display, bounds, options) {
        var resolved = getPanelBounds(bounds);
        if (!display) return resolved;
        display.dataset.subtitlePanelWidth = String(resolved.width);
        display.dataset.subtitlePanelHeight = String(resolved.height);
        display.style.width = resolved.width + 'px';
        display.style.height = resolved.height + 'px';
        display.style.minHeight = MIN_PANEL_HEIGHT + 'px';
        display.style.maxHeight = 'none';
        display.style.fontSize = '18px';
        display.style.setProperty('--subtitle-panel-width', resolved.width + 'px');
        display.style.setProperty('--subtitle-panel-height', resolved.height + 'px');
        display.style.setProperty('--subtitle-content-max-height', Math.max(24, resolved.height - 24) + 'px');
        if (!options || options.host !== 'window') {
            display.style.setProperty('--subtitle-max-width', resolved.width + 'px');
        }
        return resolved;
    }

    function applySettingsToUi(refs, state, options) {
        if (!refs || !refs.display) return;
        var host = options && options.host ? options.host : 'web';
        var passthroughEnabled = state.subtitleInteractionPassthrough !== false;
        refs.display.dataset.subtitlePanelLocked = state.subtitlePanelLocked ? 'true' : 'false';
        refs.display.dataset.subtitleInteractionPassthrough = passthroughEnabled ? 'true' : 'false';
        if (!refs.display.dataset.subtitlePanelState) {
            refs.display.dataset.subtitlePanelState = 'clean';
        }
        applyBackgroundOpacity(refs.display, state.subtitleOpacity);
        applySubtitlePanelBounds(refs.display, state.subtitlePanelBounds, { host: host });
        if (host === 'web') {
            applyWebPanelPosition(refs, state.subtitlePanelPosition);
        }
        if (refs.langSelect) {
            refs.langSelect.value = state.userLanguage;
        }
        if (refs.opacitySlider) {
            refs.opacitySlider.value = String(state.subtitleOpacity);
        }
        if (refs.opacityValue) {
            refs.opacityValue.textContent = state.subtitleOpacity + '%';
        }
        if (refs.passthroughToggle) {
            refs.passthroughToggle.checked = passthroughEnabled;
        }
    }

    function applyUiLabels(refs, state) {
        if (!refs) return;
        var locale = state && state.uiLocale ? state.uiLocale : getCurrentUiLocale();
        if (refs.labels && refs.labels.length) {
            refs.labels.forEach(function(label) {
                var key = label && label.dataset ? label.dataset.subtitleLabel : '';
                if (!key) return;
                label.textContent = getUiText(key, locale);
            });
        }
        if (refs.settingsBtn) {
            refs.settingsBtn.title = getUiText('settingsBtn', locale);
            refs.settingsBtn.setAttribute('aria-label', getUiText('settingsBtn', locale));
        }
        if (refs.lockBtn) {
            var lockKey = state && state.subtitlePanelLocked ? 'unlockPosition' : 'lockPosition';
            refs.lockBtn.title = getUiText(lockKey, locale);
            refs.lockBtn.setAttribute('aria-label', getUiText(lockKey, locale));
        }
        if (refs.closeBtn) {
            refs.closeBtn.title = getUiText('closePanel', locale);
            refs.closeBtn.setAttribute('aria-label', getUiText('closePanel', locale));
        }
        if (refs.langSelect) {
            refs.langSelect.title = getUiText('targetLang', locale);
        }
        if (refs.opacitySlider) {
            refs.opacitySlider.title = getUiText('opacity', locale);
        }
        if (refs.passthroughToggle) {
            refs.passthroughToggle.title = getUiText('passthroughInteraction', locale);
        }
        if (refs.text) {
            var placeholderLocale = normalizeUiLocale(state && state.userLanguage ? state.userLanguage : locale);
            refs.text.setAttribute('data-subtitle-placeholder', getUiFallbackText('emptyHint', placeholderLocale));
        }
    }

    function applyLockButtonIcon(lockBtn, locked) {
        if (!lockBtn) return;
        var svg = lockBtn.querySelector ? lockBtn.querySelector('svg') : null;
        var path = svg && svg.querySelector ? svg.querySelector('path') : null;
        if (!svg) {
            svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'currentColor');
            svg.setAttribute('width', '14');
            svg.setAttribute('height', '14');
            svg.setAttribute('aria-hidden', 'true');
            lockBtn.appendChild(svg);
        }
        if (!path) {
            path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            svg.appendChild(path);
        }
        lockBtn.dataset.subtitleLockIcon = locked ? 'locked' : 'unlocked';
        path.setAttribute('d', locked ? LOCK_ICON_PATH : UNLOCK_ICON_PATH);
    }

    function measureSubtitleLayout(options) {
        options = options || {};
        var text = String(options.text || '');
        var mode = options.mode || 'window';
        var bounds = getPanelBounds(options.panelBounds);
        var baseFont = options.baseFont || 18;
        var minFont = options.minFont || 12;
        var fontFamily = options.fontFamily || 'Segoe UI, Arial, sans-serif';
        var maxWidth = options.maxWidth || bounds.width;
        var minHeight = options.minHeight || bounds.height;
        var maxHeight = options.maxHeight || Math.max(minHeight, options.availableHeight || bounds.height);
        var width = mode === 'window' ? maxWidth : (options.availableWidth || maxWidth);
        var node;
        var fontSize = baseFont;
        var finalHeight = minHeight;

        if (!document.body) {
            return { width: width, height: minHeight, fontSize: baseFont };
        }
        if (!text.trim()) {
            return { width: width, height: minHeight, fontSize: baseFont };
        }

        node = document.createElement(mode === 'window' ? 'div' : 'span');
        node.style.position = 'absolute';
        node.style.visibility = 'hidden';
        node.style.left = '-9999px';
        node.style.top = '-9999px';
        node.style.boxSizing = 'border-box';
        node.style.display = 'block';
        node.style.fontSize = baseFont + 'px';
        node.style.fontWeight = '500';
        node.style.lineHeight = '1.5';
        node.style.fontFamily = fontFamily;
        node.style.whiteSpace = 'nowrap';
        if (mode === 'window') {
            node.style.padding = '12px 86px 12px 24px';
        }
        node.textContent = text;
        document.body.appendChild(node);

        if (mode === 'window') {
            width = Math.max(MIN_PANEL_WIDTH, Math.min(node.offsetWidth + 8, maxWidth));
            node.style.width = width + 'px';
        } else {
            width = Math.max(0, options.availableWidth || Math.max(0, maxWidth - PANEL_TEXT_HORIZONTAL_RESERVE));
            node.style.maxWidth = width + 'px';
            node.style.width = width + 'px';
        }
        node.style.whiteSpace = 'normal';
        node.style.overflowWrap = 'break-word';

        while (fontSize > minFont) {
            var overflowHeight = mode === 'window'
                ? node.offsetHeight + 60
                : node.offsetHeight;
            if (overflowHeight <= maxHeight) {
                break;
            }
            fontSize -= 1;
            node.style.fontSize = fontSize + 'px';
        }

        finalHeight = mode === 'window'
            ? Math.max(minHeight, Math.min(maxHeight, node.offsetHeight + 60))
            : Math.max(minHeight, node.offsetHeight);
        document.body.removeChild(node);

        return {
            width: mode === 'window' ? width : maxWidth,
            height: finalHeight,
            fontSize: fontSize
        };
    }

    function clampPanelPosition(refs, position) {
        if (!refs || !refs.display || !position) return null;
        var rect = refs.display.getBoundingClientRect ? refs.display.getBoundingClientRect() : null;
        var state = getSettings();
        var bounds = getPanelBounds(state.subtitlePanelBounds);
        var width = refs.display.offsetWidth || (rect ? rect.width : 0) || Math.min(bounds.width, window.innerWidth);
        var height = refs.display.offsetHeight || (rect ? rect.height : 0) || bounds.height;
        var maxX = Math.max(0, window.innerWidth - width);
        var maxY = Math.max(0, window.innerHeight - height);
        return {
            left: Math.max(0, Math.min(Number(position.left) || 0, maxX)),
            top: Math.max(0, Math.min(Number(position.top) || 0, maxY)),
            coordinateSpace: 'viewport'
        };
    }

    function clearWebPanelPosition(refs) {
        if (!refs || !refs.display) return;
        refs.display.style.left = '';
        refs.display.style.top = '';
        refs.display.style.bottom = '';
        refs.display.style.transform = '';
        refs.display.style.animation = '';
        delete refs.display.dataset.subtitlePositioned;
    }

    function applyWebPanelPosition(refs, position) {
        if (!refs || !refs.display) return null;
        var clamped = clampPanelPosition(refs, position);
        if (!clamped) {
            clearWebPanelPosition(refs);
            return null;
        }
        refs.display.style.left = clamped.left + 'px';
        refs.display.style.top = clamped.top + 'px';
        refs.display.style.bottom = 'auto';
        refs.display.style.transform = 'none';
        refs.display.style.animation = 'none';
        refs.display.dataset.subtitlePositioned = 'true';
        return clamped;
    }

    function attachWebDrag(refs) {
        if (!refs.display) return function() {};

        var isDragging = false;
        var pendingDrag = false;
        var currentPosition = null;
        var startX = 0;
        var startY = 0;
        var initialX = 0;
        var initialY = 0;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function canStartDrag(target) {
            if (isPanelLocked()) return false;
            if (target && target.dataset && target.dataset.resizeDir) return false;
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.panelControls && refs.panelControls.contains(target)) return false;
            if (refs.settingsBtn && refs.settingsBtn.contains(target)) return false;
            return true;
        }

        function handleMouseMove(e) {
            if (!pendingDrag && !isDragging) return;
            e.preventDefault();
            if (isPanelLocked()) {
                handleMouseUp();
                return;
            }

            var dx = e.clientX - startX;
            var dy = e.clientY - startY;
            if (!isDragging) {
                if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
                isDragging = true;
                pendingDrag = false;
                refs.display.style.animation = 'none';
                refs.display.style.transition = 'none';
                refs.display.classList.add('dragging');
                refs.display.style.transform = 'none';
                refs.display.style.left = initialX + 'px';
                refs.display.style.top = initialY + 'px';
                refs.display.style.bottom = 'auto';
            }

            currentPosition = applyWebPanelPosition(refs, {
                left: initialX + dx,
                top: initialY + dy,
                coordinateSpace: 'viewport'
            });
        }

        function handleMouseUp() {
            if (!pendingDrag && !isDragging) return;
            var shouldPersist = isDragging && currentPosition;
            pendingDrag = false;
            isDragging = false;
            document.body.style.userSelect = '';
            refs.display.classList.remove('dragging');
            refs.display.style.transition = '';
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
            if (shouldPersist) {
                updateSettings({ subtitlePanelPosition: currentPosition }, { source: 'subtitle-ui-drag' });
            }
        }

        function beginDrag(e) {
            if (!canStartDrag(e.target)) return;
            if (typeof e.button === 'number' && e.button !== 0) return;
            pendingDrag = true;
            document.body.style.userSelect = 'none';
            var rect = refs.display.getBoundingClientRect();
            startX = e.clientX;
            startY = e.clientY;
            initialX = rect.left;
            initialY = rect.top;
            currentPosition = null;
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }

        function beginTouchDrag(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            beginDrag({
                target: e.target,
                button: 0,
                clientX: touch.clientX,
                clientY: touch.clientY
            });
        }

        function handleTouchMove(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            handleMouseMove({
                preventDefault: function() { e.preventDefault(); },
                clientX: touch.clientX,
                clientY: touch.clientY
            });
        }

        function clampManualPosition() {
            var state = getSettings();
            if (!state.subtitlePanelPosition) return;
            var clamped = applyWebPanelPosition(refs, state.subtitlePanelPosition);
            if (clamped && !samePanelPosition(clamped, state.subtitlePanelPosition)) {
                updateSettings({ subtitlePanelPosition: clamped }, { source: 'subtitle-ui-position-clamp' });
            }
        }

        function onDisplayMouseDown(e) {
            beginDrag(e);
        }

        function onDisplayTouchStart(e) {
            if (!canStartDrag(e.target)) return;
            beginTouchDrag(e);
        }

        clampManualPosition();
        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('touchmove', handleTouchMove, { passive: false });
        document.addEventListener('touchend', handleMouseUp);
        document.addEventListener('touchcancel', handleMouseUp);
        window.addEventListener('resize', clampManualPosition);
        window.addEventListener('orientationchange', clampManualPosition);

        return function detachWebDrag() {
            handleMouseUp();
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('touchmove', handleTouchMove, { passive: false });
            document.removeEventListener('touchend', handleMouseUp);
            document.removeEventListener('touchcancel', handleMouseUp);
            window.removeEventListener('resize', clampManualPosition);
            window.removeEventListener('orientationchange', clampManualPosition);
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }

    function attachWindowDrag(refs, options) {
        var api = options && options.api;
        if (!refs.display || !api) return function() {};

        var isDragging = false;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function isLinuxSubtitleHost() {
            return document.body && document.body.classList.contains('subtitle-linux-host');
        }

        function bounceBackIfNeeded() {
            try {
                if (isLinuxSubtitleHost()) return;
                if (typeof window.screenX !== 'number') return;
                var margin = 30;
                var x = window.screenX;
                var y = window.screenY;
                var width = window.outerWidth;
                var height = window.outerHeight;
                var moved = false;

                if (!width || !height) return;
                if (x < 0) { x = 0; moved = true; }
                if (y < 0) { y = 0; moved = true; }
                if (x + width - margin > screen.availWidth) {
                    x = Math.max(0, screen.availWidth - width);
                    moved = true;
                }
                if (y + height - margin > screen.availHeight) {
                    y = Math.max(0, screen.availHeight - height);
                    moved = true;
                }
                if (moved) window.moveTo(x, y);
            } catch (_) {}
        }

        function canStartDrag(target) {
            if (isPanelLocked()) return false;
            if (target && target.dataset && target.dataset.resizeDir) return false;
            if (refs.settingsPanel && refs.settingsPanel.contains(target)) return false;
            if (refs.panelControls && refs.panelControls.contains(target)) return false;
            if (refs.settingsBtn && refs.settingsBtn.contains(target)) return false;
            return true;
        }

        function startDrag(e) {
            if (!canStartDrag(e.target)) return;
            isDragging = true;
            if (typeof api.dragStart === 'function') {
                api.dragStart();
            }
            if (e.preventDefault) e.preventDefault();
        }

        function stopDrag() {
            if (!isDragging) return;
            isDragging = false;
            if (typeof api.dragStop === 'function') {
                api.dragStop();
            }
            bounceBackIfNeeded();
        }

        function onDisplayMouseDown(e) {
            startDrag(e);
        }

        function onDisplayTouchStart(e) {
            startDrag(e);
        }

        refs.display.addEventListener('mousedown', onDisplayMouseDown);
        refs.display.addEventListener('touchstart', onDisplayTouchStart, { passive: false });
        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
        window.addEventListener('focus', bounceBackIfNeeded);
        window.addEventListener('resize', bounceBackIfNeeded);

        return function detachWindowDrag() {
            stopDrag();
            refs.display.classList.remove('dragging');
            refs.display.removeEventListener('mousedown', onDisplayMouseDown);
            refs.display.removeEventListener('touchstart', onDisplayTouchStart, { passive: false });
            document.removeEventListener('mouseup', stopDrag);
            document.removeEventListener('touchend', stopDrag);
            document.removeEventListener('touchcancel', stopDrag);
            window.removeEventListener('focus', bounceBackIfNeeded);
            window.removeEventListener('resize', bounceBackIfNeeded);
        };
    }

    function getResizeCursor(dir) {
        if (dir === 'n' || dir === 's') return 'ns-resize';
        if (dir === 'e' || dir === 'w') return 'ew-resize';
        if (dir === 'ne' || dir === 'sw') return 'nesw-resize';
        return 'nwse-resize';
    }

    function calculateResizeBounds(start, dir, clientX, clientY, limits) {
        var dx = clientX - start.x;
        var dy = clientY - start.y;
        var left = start.left;
        var top = start.top;
        var width = start.width;
        var height = start.height;

        if (dir.indexOf('e') !== -1) {
            width = Math.max(MIN_PANEL_WIDTH, start.width + dx);
        }
        if (dir.indexOf('s') !== -1) {
            height = Math.max(MIN_PANEL_HEIGHT, start.height + dy);
        }
        if (dir.indexOf('w') !== -1) {
            width = Math.max(MIN_PANEL_WIDTH, start.width - dx);
            left = start.left + start.width - width;
        }
        if (dir.indexOf('n') !== -1) {
            height = Math.max(MIN_PANEL_HEIGHT, start.height - dy);
            top = start.top + start.height - height;
        }

        if (limits && limits.clampToViewport) {
            if (left < 0) {
                width = Math.max(MIN_PANEL_WIDTH, width + left);
                left = 0;
            }
            if (top < 0) {
                height = Math.max(MIN_PANEL_HEIGHT, height + top);
                top = 0;
            }
            if (left + width > limits.width) {
                width = Math.max(MIN_PANEL_WIDTH, limits.width - left);
            }
            if (top + height > limits.height) {
                height = Math.max(MIN_PANEL_HEIGHT, limits.height - top);
            }
        }

        return {
            bounds: getPanelBounds({ width: width, height: height }),
            position: {
                left: Math.max(0, Math.round(left)),
                top: Math.max(0, Math.round(top)),
                coordinateSpace: 'viewport'
            }
        };
    }

    function attachPanelResize(refs, options) {
        if (!refs.display || !refs.resizeHandles || !refs.resizeHandles.length) {
            return function() {};
        }

        var api = options && options.api;
        var host = options && options.host ? options.host : 'web';
        var windowEdgeInset = host === 'window' ? Math.max(0, Number(options && options.windowEdgeInset) || 0) : 0;
        var resizeState = null;

        function isPanelLocked() {
            return !!getSettings().subtitlePanelLocked;
        }

        function getStartMetrics(e, dir) {
            var rect = refs.display.getBoundingClientRect();
            var bounds = getPanelBounds({
                width: rect.width || refs.display.offsetWidth,
                height: rect.height || refs.display.offsetHeight
            });
            if (host === 'window') {
                return {
                    dir: dir,
                    x: e.clientX,
                    y: e.clientY,
                    left: typeof window.screenX === 'number' ? window.screenX : 0,
                    top: typeof window.screenY === 'number' ? window.screenY : 0,
                    width: bounds.width,
                    height: bounds.height
                };
            }
            return {
                dir: dir,
                x: e.clientX,
                y: e.clientY,
                left: rect.left,
                top: rect.top,
                width: bounds.width,
                height: bounds.height
            };
        }

        function applyResize(result, persist) {
            applySubtitlePanelBounds(refs.display, result.bounds, { host: host });
            if (host === 'web') {
                applyWebPanelPosition(refs, result.position);
            } else if (api) {
                if (typeof api.setPosition === 'function' &&
                    (resizeState.dir.indexOf('n') !== -1 || resizeState.dir.indexOf('w') !== -1)) {
                    api.setPosition(result.position.left, result.position.top);
                }
                if (typeof api.setSize === 'function') {
                    api.setSize(
                        result.bounds.width + windowEdgeInset * 2,
                        result.bounds.height + windowEdgeInset * 2,
                        {
                            panelBounds: result.bounds
                        }
                    );
                }
            }
            if (!persist) return;
            var patch = { subtitlePanelBounds: result.bounds };
            if (host === 'web') {
                patch.subtitlePanelPosition = result.position;
            }
            var nextState = updateSettings(patch, { source: 'subtitle-ui-resize' });
            if (typeof options.propagateSetting === 'function') {
                options.propagateSetting({
                    type: 'bounds',
                    value: result.bounds,
                    patch: { subtitlePanelBounds: result.bounds },
                    state: nextState
                });
            }
        }

        function updateResize(clientX, clientY, persist) {
            if (!resizeState) return;
            resizeState.lastX = clientX;
            resizeState.lastY = clientY;
            var result = calculateResizeBounds(resizeState, resizeState.dir, clientX, clientY, {
                clampToViewport: host === 'web',
                width: window.innerWidth,
                height: window.innerHeight
            });
            applyResize(result, persist);
        }

        function onMove(e) {
            if (!resizeState) return;
            e.preventDefault();
            updateResize(e.clientX, e.clientY, false);
        }

        function onTouchMove(e) {
            if (!resizeState || !e.touches || !e.touches.length) return;
            e.preventDefault();
            updateResize(e.touches[0].clientX, e.touches[0].clientY, false);
        }

        function endResize(e) {
            if (!resizeState) return;
            var clientX = e && typeof e.clientX === 'number' ? e.clientX : resizeState.lastX;
            var clientY = e && typeof e.clientY === 'number' ? e.clientY : resizeState.lastY;
            if ((typeof clientX !== 'number' || typeof clientY !== 'number') &&
                e && e.changedTouches && e.changedTouches.length) {
                clientX = e.changedTouches[0].clientX;
                clientY = e.changedTouches[0].clientY;
            }
            if (typeof clientX !== 'number') clientX = resizeState.x;
            if (typeof clientY !== 'number') clientY = resizeState.y;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            refs.display.classList.remove('resizing');
            refs.display.style.transition = '';
            updateResize(clientX, clientY, true);
            resizeState = null;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endResize);
            document.removeEventListener('touchmove', onTouchMove, { passive: false });
            document.removeEventListener('touchend', endResize);
            document.removeEventListener('touchcancel', endResize);
        }

        function beginResize(e, dir) {
            if (isPanelLocked()) return;
            if (typeof e.button === 'number' && e.button !== 0) return;
            resizeState = getStartMetrics(e, dir);
            resizeState.lastX = resizeState.x;
            resizeState.lastY = resizeState.y;
            refs.display.classList.add('resizing');
            refs.display.style.transition = 'none';
            document.body.style.userSelect = 'none';
            document.body.style.cursor = getResizeCursor(dir);
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
            document.addEventListener('mousemove', onMove);
            document.addEventListener('touchmove', onTouchMove, { passive: false });
            document.addEventListener('mouseup', endResize);
            document.addEventListener('touchend', endResize);
            document.addEventListener('touchcancel', endResize);
        }

        refs.resizeHandles.forEach(function(handle) {
            var dir = handle.dataset.resizeDir || 'se';
            var onMouseDown = function(e) { beginResize(e, dir); };
            var onTouchStart = function(e) {
                if (!e.touches || !e.touches.length) return;
                beginResize({
                    target: e.target,
                    button: 0,
                    clientX: e.touches[0].clientX,
                    clientY: e.touches[0].clientY,
                    preventDefault: function() { e.preventDefault(); },
                    stopPropagation: function() { e.stopPropagation(); }
                }, dir);
            };
            handle.addEventListener('mousedown', onMouseDown);
            handle.addEventListener('touchstart', onTouchStart, { passive: false });
            handle._nekoSubtitleResizeCleanup = function() {
                handle.removeEventListener('mousedown', onMouseDown);
                handle.removeEventListener('touchstart', onTouchStart, { passive: false });
            };
        });

        return function detachPanelResize() {
            if (resizeState) {
                endResize({
                    clientX: resizeState.lastX,
                    clientY: resizeState.lastY
                });
            } else {
                document.body.style.userSelect = '';
                document.body.style.cursor = '';
                refs.display.classList.remove('resizing');
                refs.display.style.transition = '';
            }
            refs.resizeHandles.forEach(function(handle) {
                if (typeof handle._nekoSubtitleResizeCleanup === 'function') {
                    handle._nekoSubtitleResizeCleanup();
                    delete handle._nekoSubtitleResizeCleanup;
                }
            });
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endResize);
            document.removeEventListener('touchmove', onTouchMove, { passive: false });
            document.removeEventListener('touchend', endResize);
            document.removeEventListener('touchcancel', endResize);
        };
    }

    function initSubtitleUI(options) {
        options = options || {};
        var refs = getSubtitleRefs(options.root || document);
        var cleanupFns = [];
        var state = getSettings();
        var controlsHideTimer = null;
        var panelState = normalizePanelState((getRenderState() || {}).subtitlePanelState);
        var externalSettingsOpen = false;

        if (!refs.display) {
            return null;
        }

        function notifyPanelStateChanged(source) {
            if (typeof options.onSettingsApplied === 'function') {
                options.onSettingsApplied(getSettings(), refs, {
                    changedKeys: ['subtitlePanelState'],
                    source: source || 'subtitle-ui-panel-state'
                });
            }
        }

        function applyPanelState(nextState, source) {
            var previousState = panelState;
            panelState = normalizePanelState(nextState);
            refs.display.dataset.subtitlePanelState = panelState;
            if (refs.panelControls) {
                refs.panelControls.setAttribute('aria-hidden', panelState === 'clean' ? 'true' : 'false');
            }
            if (refs.settingsBtn) {
                refs.settingsBtn.setAttribute('aria-expanded', panelState === 'settings' ? 'true' : 'false');
            }
            if (previousState === panelState && source !== 'subtitle-ui-init') {
                return;
            }
            updateRenderState({ subtitlePanelState: panelState }, {
                source: source || 'subtitle-ui-panel-state'
            });
            notifyPanelStateChanged(source);
        }

        function clearControlsHideTimer() {
            if (controlsHideTimer) {
                clearTimeout(controlsHideTimer);
                controlsHideTimer = null;
            }
        }

        function isInlineSettingsOpen() {
            return !!(refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden'));
        }

        function isSettingsOpen() {
            return externalSettingsOpen || isInlineSettingsOpen();
        }

        function hasFocusWithinPanel() {
            return !!(document.activeElement && refs.display.contains(document.activeElement));
        }

        function showControls(source) {
            clearControlsHideTimer();
            if (panelState !== 'settings') {
                applyPanelState('controls', source || 'subtitle-ui-controls');
            }
        }

        function scheduleClean(source) {
            clearControlsHideTimer();
            if (isSettingsOpen()) return;
            controlsHideTimer = setTimeout(function() {
                controlsHideTimer = null;
                if (isSettingsOpen()) return;
                if (refs.display.matches && refs.display.matches(':hover')) return;
                if (hasFocusWithinPanel()) return;
                applyPanelState('clean', source || 'subtitle-ui-clean');
            }, CONTROLS_HIDE_DELAY_MS);
        }

        function openSettings(source) {
            if (typeof options.openExternalSettings === 'function') {
                clearControlsHideTimer();
                externalSettingsOpen = true;
                applyPanelState('settings', source || 'subtitle-ui-settings-open');
                options.openExternalSettings(getSettings(), refs, {
                    source: source || 'subtitle-ui-settings-open'
                });
                if (refs.settingsBtn && typeof refs.settingsBtn.blur === 'function') {
                    refs.settingsBtn.blur();
                }
                return;
            }
            if (!refs.settingsPanel) return;
            clearControlsHideTimer();
            refs.settingsPanel.classList.remove('hidden');
            applyPanelState('settings', source || 'subtitle-ui-settings-open');
        }

        function closeSettings(source, nextPanelState) {
            var wasExternalSettingsOpen = externalSettingsOpen;
            if (wasExternalSettingsOpen && typeof options.closeExternalSettings === 'function') {
                externalSettingsOpen = false;
                options.closeExternalSettings({
                    source: source || 'subtitle-ui-settings-close'
                });
            } else {
                externalSettingsOpen = false;
            }
            if (refs.settingsPanel) {
                refs.settingsPanel.classList.add('hidden');
            }
            applyPanelState(nextPanelState || 'controls', source || 'subtitle-ui-settings-close');
            if (wasExternalSettingsOpen && nextPanelState !== 'clean') {
                if (refs.settingsBtn && typeof refs.settingsBtn.blur === 'function') {
                    refs.settingsBtn.blur();
                }
                scheduleClean(source || 'subtitle-ui-settings-close');
            }
        }

        function hasHostCloseBridge() {
            return !!(options.api && typeof options.api.changeSettings === 'function');
        }

        function hideLocalPanelAfterClose(source) {
            refs.display.classList.remove('show');
            refs.display.classList.add('hidden');
            updateRenderState({
                visible: false,
                subtitleEnabled: false
            }, {
                source: source || 'subtitle-ui-close-local'
            });
        }

        function applyState(nextState, detail) {
            applySettingsToUi(refs, nextState, options);
            applyUiLabels(refs, nextState);
            if (!refs.display.dataset.subtitlePanelState) {
                refs.display.dataset.subtitlePanelState = panelState;
            }
            if (refs.lockBtn) {
                refs.lockBtn.setAttribute('aria-pressed', nextState.subtitlePanelLocked ? 'true' : 'false');
                applyLockButtonIcon(refs.lockBtn, !!nextState.subtitlePanelLocked);
            }
            if (typeof options.onSettingsApplied === 'function') {
                options.onSettingsApplied(nextState, refs, detail || { changedKeys: [], source: 'init' });
            }
        }

        applyState(state, { changedKeys: [], source: 'init' });
        applyPanelState(panelState, 'subtitle-ui-init');
        cleanupFns.push(subscribeSettings(applyState, { immediate: false }));

        function setPanelLocked(nextLocked, source) {
            var locked = !!nextLocked;
            var nextState = updateSettings({ subtitlePanelLocked: locked }, {
                source: source || 'subtitle-ui-lock'
            });
            if (typeof options.propagateSetting === 'function') {
                options.propagateSetting({
                    type: 'lock',
                    value: locked,
                    patch: { subtitlePanelLocked: locked },
                    state: nextState
                });
            }
            return nextState;
        }

        var observedThemeDark = isDarkThemeActive();
        var applyThemeStateIfChanged = function(source) {
            var nextThemeDark = isDarkThemeActive();
            if (nextThemeDark === observedThemeDark) return;
            observedThemeDark = nextThemeDark;
            applyState(getSettings(), { changedKeys: ['theme'], source: source });
        };
        var onThemeChanged = function() {
            applyThemeStateIfChanged('subtitle-ui-theme-event');
        };
        window.addEventListener('neko-theme-changed', onThemeChanged);
        cleanupFns.push(function() {
            window.removeEventListener('neko-theme-changed', onThemeChanged);
        });
        if (window.MutationObserver && document.documentElement) {
            var themeObserver = new MutationObserver(function(mutations) {
                for (var i = 0; i < mutations.length; i += 1) {
                    if (mutations[i].attributeName === 'data-theme') {
                        applyThemeStateIfChanged('subtitle-ui-theme-attribute');
                        break;
                    }
                }
            });
            themeObserver.observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['data-theme']
            });
            cleanupFns.push(function() {
                themeObserver.disconnect();
            });
        }

        if (window.i18next && typeof window.i18next.on === 'function') {
            var onLanguageChanged = function(nextLocale) {
                updateSettings({ uiLocale: nextLocale }, {
                    persist: false,
                    source: 'subtitle-ui-locale'
                });
            };
            window.i18next.on('languageChanged', onLanguageChanged);
            cleanupFns.push(function() {
                if (window.i18next && typeof window.i18next.off === 'function') {
                    window.i18next.off('languageChanged', onLanguageChanged);
                }
            });
        }

        if (refs.display) {
            var onPanelPointerEnter = function() {
                showControls('subtitle-ui-pointerenter');
            };
            var onPanelPointerLeave = function() {
                scheduleClean('subtitle-ui-pointerleave');
            };
            var onPanelClick = function(e) {
                if (refs.settingsPanel && refs.settingsPanel.contains(e.target)) return;
                if (refs.panelControls && refs.panelControls.contains(e.target)) return;
                showControls('subtitle-ui-click');
            };
            var onPanelFocusIn = function() {
                showControls('subtitle-ui-focusin');
            };
            var onPanelFocusOut = function() {
                setTimeout(function() {
                    if (!hasFocusWithinPanel()) {
                        scheduleClean('subtitle-ui-focusout');
                    }
                }, 0);
            };
            var onPanelKeyDown = function(e) {
                if (e.key !== 'Escape') return;
                if (isSettingsOpen()) {
                    closeSettings('subtitle-ui-escape-settings', 'controls');
                    e.stopPropagation();
                    return;
                }
                applyPanelState('clean', 'subtitle-ui-escape-clean');
            };

            refs.display.addEventListener('pointerenter', onPanelPointerEnter);
            refs.display.addEventListener('pointerleave', onPanelPointerLeave);
            refs.display.addEventListener('click', onPanelClick);
            refs.display.addEventListener('focusin', onPanelFocusIn);
            refs.display.addEventListener('focusout', onPanelFocusOut);
            refs.display.addEventListener('keydown', onPanelKeyDown);
            cleanupFns.push(function() {
                refs.display.removeEventListener('pointerenter', onPanelPointerEnter);
                refs.display.removeEventListener('pointerleave', onPanelPointerLeave);
                refs.display.removeEventListener('click', onPanelClick);
                refs.display.removeEventListener('focusin', onPanelFocusIn);
                refs.display.removeEventListener('focusout', onPanelFocusOut);
                refs.display.removeEventListener('keydown', onPanelKeyDown);
            });
        }

        if (refs.lockBtn) {
            var onLockClick = function(e) {
                e.stopPropagation();
                showControls('subtitle-ui-lock');
                var nextLocked = !getSettings().subtitlePanelLocked;
                setPanelLocked(nextLocked, 'subtitle-ui-lock');
            };
            refs.lockBtn.addEventListener('click', onLockClick);
            cleanupFns.push(function() {
                refs.lockBtn.removeEventListener('click', onLockClick);
            });
        }

        if (refs.closeBtn) {
            var onCloseClick = function(e) {
                e.stopPropagation();
                closeSettings('subtitle-ui-close', 'clean');
                if (typeof options.onClose === 'function') {
                    options.onClose();
                } else if (typeof options.propagateSetting === 'function') {
                    var nextState = updateSettings({ subtitleEnabled: false }, { source: 'subtitle-ui-close' });
                    options.propagateSetting({
                        type: 'toggle',
                        value: false,
                        patch: { subtitleEnabled: false },
                        state: nextState
                    });
                    if (!hasHostCloseBridge()) {
                        hideLocalPanelAfterClose('subtitle-ui-close-fallback');
                    }
                } else {
                    updateSettings({ subtitleEnabled: false }, { source: 'subtitle-ui-close' });
                    hideLocalPanelAfterClose('subtitle-ui-close-fallback');
                }
            };
            refs.closeBtn.addEventListener('click', onCloseClick);
            cleanupFns.push(function() {
                refs.closeBtn.removeEventListener('click', onCloseClick);
            });
        }

        if (refs.settingsBtn) {
            var onSettingsClick = function(e) {
                e.stopPropagation();
                if (externalSettingsOpen) {
                    closeSettings('subtitle-ui-panel', 'controls');
                } else if (typeof options.openExternalSettings === 'function') {
                    openSettings('subtitle-ui-panel');
                } else if (isSettingsOpen()) {
                    closeSettings('subtitle-ui-panel', 'controls');
                } else {
                    openSettings('subtitle-ui-panel');
                }
            };
            var onDocumentDown = function(e) {
                if (!isSettingsOpen()) return;
                if (refs.display.contains(e.target)) return;
                closeSettings('subtitle-ui-panel-outside', 'clean');
            };
            refs.settingsBtn.addEventListener('click', onSettingsClick);
            document.addEventListener('mousedown', onDocumentDown);
            cleanupFns.push(function() {
                refs.settingsBtn.removeEventListener('click', onSettingsClick);
                document.removeEventListener('mousedown', onDocumentDown);
            });
        }

        if (refs.langSelect) {
            var onLanguageSelect = function() {
                var nextLanguage = normalizeTranslationLanguageCode(refs.langSelect.value);
                var nextState = updateSettings({ userLanguage: nextLanguage }, { source: 'subtitle-ui-language' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'language',
                        value: nextLanguage,
                        patch: { userLanguage: nextLanguage },
                        state: nextState
                    });
                }
                if (typeof options.onLanguageChange === 'function') {
                    options.onLanguageChange(nextLanguage, nextState);
                }
            };
            refs.langSelect.addEventListener('change', onLanguageSelect);
            cleanupFns.push(function() {
                refs.langSelect.removeEventListener('change', onLanguageSelect);
            });
        }

        if (refs.opacitySlider) {
            var onOpacityInput = function() {
                var nextOpacity = clampOpacity(refs.opacitySlider.value);
                var nextState = updateSettings({ subtitleOpacity: nextOpacity }, { source: 'subtitle-ui-opacity' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'opacity',
                        value: nextOpacity,
                        patch: { subtitleOpacity: nextOpacity },
                        state: nextState
                    });
                }
            };
            refs.opacitySlider.addEventListener('input', onOpacityInput);
            cleanupFns.push(function() {
                refs.opacitySlider.removeEventListener('input', onOpacityInput);
            });
        }

        if (refs.passthroughToggle) {
            var onPassthroughChange = function() {
                var nextPassthrough = !!refs.passthroughToggle.checked;
                var nextState = updateSettings({ subtitleInteractionPassthrough: nextPassthrough }, { source: 'subtitle-ui-passthrough' });
                if (typeof options.propagateSetting === 'function') {
                    options.propagateSetting({
                        type: 'interactionPassthrough',
                        value: nextPassthrough,
                        patch: { subtitleInteractionPassthrough: nextPassthrough },
                        state: nextState
                    });
                }
            };
            refs.passthroughToggle.addEventListener('change', onPassthroughChange);
            cleanupFns.push(function() {
                refs.passthroughToggle.removeEventListener('change', onPassthroughChange);
            });
        }

        if (options.windowInteractions === 'external') {
            refs.display.dataset.subtitleWindowInteractions = 'external';
        } else {
            cleanupFns.push(attachPanelResize(refs, options));
            cleanupFns.push(options.host === 'window' ? attachWindowDrag(refs, options) : attachWebDrag(refs));
        }

        return {
            refs: refs,
            applyCurrentState: function() {
                applyState(getSettings(), { changedKeys: [], source: 'manual' });
            },
            closeSettingsForExternalInteraction: function(nextPanelState) {
                closeSettings('subtitle-ui-external-interaction', nextPanelState || 'controls');
            },
            destroy: function() {
                clearControlsHideTimer();
                while (cleanupFns.length) {
                    var fn = cleanupFns.pop();
                    if (typeof fn === 'function') fn();
                }
            }
        };
    }

    ensureSettingsState();
    ensureRenderState();

    window.nekoSubtitleShared = {
        SETTINGS_EVENT: SETTINGS_EVENT,
        RENDER_EVENT: RENDER_EVENT,
        getSettings: getSettings,
        updateSettings: updateSettings,
        getRenderState: getRenderState,
        updateRenderState: updateRenderState,
        subscribeSettings: subscribeSettings,
        subscribeRenderState: subscribeRenderState,
        normalizeTranslationLanguageCode: normalizeTranslationLanguageCode,
        normalizeUiLocale: normalizeUiLocale,
        getCurrentUiLocale: getCurrentUiLocale,
        getUiText: getUiText,
        applyBackgroundOpacity: applyBackgroundOpacity,
        measureSubtitleLayout: measureSubtitleLayout,
        getPanelBounds: getPanelBounds,
        applySubtitlePanelBounds: applySubtitlePanelBounds,
        initSubtitleUI: initSubtitleUI
    };
})();
